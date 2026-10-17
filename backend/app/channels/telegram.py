"""Telegram via python-telegram-bot. The worker runs long polling (exactly one instance);
API instances only need the Bot object to send."""
import asyncio
import logging
import re
from html import escape

from sqlmodel import Session, select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyParameters, Update
from telegram.constants import ChatType
from telegram.error import NetworkError, RetryAfter
from telegram.ext import (
    AIORateLimiter,
    Application,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

from .. import channels, github, service
from ..config import settings
from ..db import engine
from ..models import Member, Setting, Task

log = logging.getLogger("telegram")


class TelegramChannel:
    name = "telegram"

    def __init__(self):
        self.app: Application | None = None
        self.username: str | None = None
        self.polling = False

    def ready(self) -> bool:
        return self.app is not None

    def linked(self, m: Member) -> bool:
        return bool(m.tg_chat_id)

    async def send(self, m: Member, item: dict) -> str | None:
        markup = None
        if item["buttons"]:
            markup = InlineKeyboardMarkup([[InlineKeyboardButton(lbl, callback_data=d) for lbl, d in row]
                                           for row in item["buttons"]])
        sent = await self.app.bot.send_message(m.tg_chat_id, item["text"], parse_mode="HTML", reply_markup=markup)
        return str(sent.message_id)

    async def send_group(self, chat_id: int, html_text: str, markup=None, reply_to: int | None = None) -> str:
        """Post to a group (optionally as a reply, with buttons). Retries transient failures; raises if Telegram stays unreachable."""
        keyboard = (InlineKeyboardMarkup([[InlineKeyboardButton(lbl, callback_data=d) for lbl, d in row] for row in markup])
                    if markup else None)
        reply = ReplyParameters(message_id=int(reply_to), allow_sending_without_reply=True) if reply_to else None
        for attempt in range(3):
            try:
                sent = await self.app.bot.send_message(chat_id, html_text, parse_mode="HTML", reply_markup=keyboard,
                                                       reply_parameters=reply)
                return str(sent.message_id)
            except RetryAfter as e:                                   # flood control: wait as told
                await asyncio.sleep(min(float(e.retry_after), 30))
            except NetworkError:
                await asyncio.sleep(2 ** attempt)
        raise RuntimeError("Telegram unavailable after 3 attempts")

    # ------------------------------------------------------------ lifecycle
    async def start(self, poll: bool) -> None:
        if not settings.telegram_enabled:
            return
        # Telegram answers in ~1.5 s from here with occasional long spikes; the library's 5 s defaults turned those into failures
        self.app = (Application.builder().token(settings.telegram_token).rate_limiter(AIORateLimiter())
                    .connect_timeout(20).read_timeout(40).write_timeout(40).pool_timeout(20).get_updates_read_timeout(50).build())
        for h in (CommandHandler("start", start), CommandHandler("checkin", checkin),
                  CommandHandler("status", status), CommandHandler("help", help_cmd), CommandHandler("task", task_cmd),
                  CommandHandler("join", join_cmd), CommandHandler("setgroup", setgroup_cmd), CommandHandler("deps", deps_cmd),
                  CommandHandler("prd", prd_cmd), CommandHandler("reviewpr", reviewpr_cmd),
                  ChatMemberHandler(on_bot_added, ChatMemberHandler.MY_CHAT_MEMBER),
                  CallbackQueryHandler(on_button), MessageHandler(filters.TEXT & ~filters.COMMAND, on_text),
                  MessageHandler(filters.Document.ALL, on_document), MessageHandler(filters.VOICE, on_voice)):
            self.app.add_handler(h)
        self.app.add_handler(TypeHandler(Update, stamp_update), group=-1)       # records that Telegram really reached us
        await self.app.initialize()
        self.username = (await self.app.bot.get_me()).username
        try:                                            # the menu Telegram shows when someone types "/"
            await self.app.bot.set_my_commands([
                ("join", "Tell me who you are (works in the group)"), ("task", "Add a task: /task Title @name due friday est 3h"),
                ("status", "Project board"), ("checkin", "Ask everyone for updates (lead)"),
                ("deps", "Link the obvious prerequisites between tasks"),
                ("prd", "Ingest a Markdown/text PRD (lead, attach a file)"),
                ("reviewpr", "Read-only review of an allowlisted PR URL (lead)"),
                ("setgroup", "Make this group the project group (lead)"), ("help", "What I can do")])
        except Exception as e:
            log.warning("could not set the command menu: %s", e)
        if poll:
            await self.app.start()
            await self.app.updater.start_polling(drop_pending_updates=True)
            self.polling = True
            log.info("Telegram @%s polling", self.username)
        else:
            log.info("Telegram @%s send-only (polling runs in the worker)", self.username)

    async def stop(self) -> None:
        if self.app and self.polling:
            await self.app.updater.stop()
            await self.app.stop()
        if self.app:
            await self.app.shutdown()


CHANNEL = TelegramChannel()
channels.register(CHANNEL)


def _member(s: Session, update: Update) -> Member | None:
    m = s.exec(select(Member).where(Member.tg_user_id == update.effective_user.id)).first()
    uname = getattr(update.effective_user, "username", None)
    if m is not None and uname and m.tg_username != uname:       # learn (and keep current) the @username people are tagged by
        m.tg_username = uname
        s.add(m)
        s.commit()
    return m


def resolve_mentions(s: Session, text: str, bot_username: str | None = None) -> str:
    """"@JustSoham the deployment is stopped" -> "Soham the deployment is stopped": team members tagged by their Telegram
    @username become their team name (so the rest of the pipeline sees who is meant); the bot's own @tag is dropped."""
    names = {(m.tg_username or "").lower(): m.name for m in s.exec(select(Member)).all() if m.tg_username}

    def sub(m):
        u = m.group(1).lower()
        if bot_username and u == bot_username.lower():
            return ""
        return names.get(u, m.group(0))
    return re.sub(r"\s{2,}", " ", re.sub(r"@(\w{3,32})", sub, text)).strip()


def _link(s: Session, m: Member, update: Update) -> None:
    """Identify the sender as team member `m`. In a private chat the chat id is stored too (the agent can DM them).
    In a group only the user id is stored: that is enough for @mentions and for knowing who wrote what, and the group
    is never mistaken for their private chat."""
    uid = update.effective_user.id
    for other in s.exec(select(Member).where(Member.tg_user_id == uid)).all():
        if other.id != m.id:                                      # one Telegram account is one team member
            other.tg_user_id = other.tg_chat_id = None
            s.add(other)
    if settings.lead_telegram_id and str(uid) == settings.lead_telegram_id:
        m.role = "lead"
    ids: dict = {"tg_user_id": uid}
    if getattr(update.effective_user, "username", None):
        ids["tg_username"] = update.effective_user.username
    if update.effective_chat.type == ChatType.PRIVATE:
        ids["tg_chat_id"] = update.effective_chat.id
    channels.link(s, m, "telegram", **ids)


def _remember_group(s: Session, chat, force: bool = False) -> bool:
    """Remember a group as the project group. Without `force` the first one wins and a configured
    TELEGRAM_GROUP_CHAT_ID is never overridden. Returns True if this chat was just saved."""
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return False
    if settings.telegram_group_chat_id:
        return False
    row = s.get(Setting, "tg_group_chat_id")
    if row is not None and not force:
        return False
    if row is not None and row.value == str(chat.id):
        return False
    if row is None:
        s.add(Setting(key="tg_group_chat_id", value=str(chat.id)))
    else:
        row.value = str(chat.id)
        s.add(row)
    s.commit()
    log.info("project group chat set: %s", chat.id)
    return True


def _picker(s: Session) -> InlineKeyboardMarkup | None:
    """One button per team member who has not identified themselves on Telegram yet (None when everybody has)."""
    free = [x for x in s.exec(select(Member)).all() if not x.tg_user_id]
    return InlineKeyboardMarkup([[InlineKeyboardButton(x.name, callback_data=f"iam:{x.key}")] for x in free]) if free else None


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    with Session(engine) as s:
        key = ctx.args[0].lower() if ctx.args else ""
        m = s.exec(select(Member).where(Member.key == key)).first() if key else None
        if m is None:
            existing = _member(s, update)
            if existing:
                await update.message.reply_text(f"You're connected as {existing.name}.")
                return
            kb = _picker(s)
            if kb is None:
                await update.message.reply_text("Everyone on the team is already connected.")
                return
            await update.message.reply_text("Hi! Which team member are you?", reply_markup=kb)
            return
        _link(s, m, update)
        name = m.name                                  # read while the session is open: a closed session cannot load it
    where = "here" if update.effective_chat.type == ChatType.PRIVATE else "in the group"
    await update.message.reply_html(f"✅ Connected as <b>{escape(name)}</b>. I'll check in about your tasks {where}.")


async def join_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/join: tap your name so the agent knows who you are. Works in the group, no private chat needed."""
    with Session(engine) as s:
        existing = _member(s, update)
        kb = _picker(s)
    if existing:
        await update.message.reply_text(f"You're already connected as {existing.name}.")
    elif kb is None:
        await update.message.reply_text("Everyone on the team is already connected. If your name is missing, ask the lead to add you to team.csv.")
    else:
        await update.message.reply_text("Tap your name so I know who is who:", reply_markup=kb)


async def deps_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/deps: look at the open tasks and link the obvious prerequisites."""
    from .. import converse
    with Session(engine) as s:
        if _member(s, update) is None:
            await update.message.reply_text("Send /join first so I know who you are.")
            return
        asyncio.create_task(_typing(ctx, update.effective_chat.id))
        try:
            linked = await converse.infer_dependencies(s)
        except ValueError as e:
            await update.message.reply_text(str(e))
            return
    await update.message.reply_html("🔗 " + "\n🔗 ".join(escape(x) for x in linked) if linked
                                    else "I do not see any obvious dependencies that are not linked already.")


async def _prd_source(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Get bounded text only from an authorized lead and an approved Telegram/GitHub source."""
    from .. import prd as prd_service
    message = update.effective_message
    chat = update.effective_chat
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await message.reply_text("Use PRD ingestion in the configured team group.")
        return None
    document = getattr(message, "document", None)
    filename = getattr(document, "file_name", "") if document else ""
    source_ref = f"telegram:{chat.id}:{message.message_id}"
    if document:
        if not prd_service.accepted_filename(filename):
            await message.reply_text("Only Markdown (.md) and plain text (.txt) PRD uploads are accepted.")
            return None
        if getattr(document, "file_size", 0) > prd_service.MAX_SOURCE_BYTES:
            await message.reply_text("PRD exceeds the 1 MiB upload limit.")
            return None
        try:
            remote = await ctx.bot.get_file(document.file_id)
            raw = await remote.download_as_bytearray()
            if len(raw) > prd_service.MAX_SOURCE_BYTES:
                raise ValueError("oversized file")
            text = bytes(raw).decode("utf-8")
        except (UnicodeDecodeError, ValueError):
            await message.reply_text("PRD must be valid UTF-8 text and under 1 MiB.")
            return None
        except Exception as exc:
            log.warning("Telegram PRD download failed: %s", type(exc).__name__)
            await message.reply_text("Could not download the PRD from Telegram.")
            return None
    else:
        parts = (message.text or message.caption or "").split(maxsplit=1)
        url = parts[1].strip() if len(parts) > 1 else ""
        allowed = {x.strip() for x in settings.github_allowed_repos.split(",") if x.strip()}
        source = prd_service.parse_prd_file_url(url, allowed)
        if source is None:
            await message.reply_text("Attach a .md/.txt PRD or supply an allowlisted GitHub file URL after /prd.")
            return None
        owner, repo, branch, path = source
        try:
            text = await prd_service.fetch_github_text(owner, repo, branch, path, settings.github_token)
        except Exception as exc:
            log.warning("GitHub PRD fetch failed: %s", type(exc).__name__)
            await message.reply_text("Could not fetch that allowlisted GitHub PRD file.")
            return None
        filename = path.rsplit("/", 1)[-1]
        source_ref = f"github:{owner}/{repo}/{branch}/{path}"
    with Session(engine) as s:
        lead = _member(s, update)
        if lead is None or lead.role != "lead":
            await message.reply_text("Only a linked team lead can ingest a PRD. Send /join first if needed.")
            return None
        configured = channels.group_chat_id(s)
        if configured and int(chat.id) != int(configured):
            await message.reply_text("PRD ingestion is restricted to the configured project group.")
            return None
    return text, source_ref, filename, lead.id


async def prd_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/prd <GitHub file URL> or reply to a Markdown/text file upload with /prd."""
    source = await _prd_source(update, ctx)
    if not source:
        return
    text, source_ref, filename, lead_id = source
    from .. import prd as prd_service
    with Session(engine) as s:
        lead = s.get(Member, lead_id)
        try:
            result = await prd_service.ingest_async(s, lead, text, source_ref=source_ref, source_name=filename)
        except (PermissionError, ValueError) as exc:
            await update.effective_message.reply_text(str(exc))
            return
        except Exception:
            log.exception("PRD ingestion failed")
            await update.effective_message.reply_text("PRD analysis failed; no completion was reported.")
            return
        lines = [f"Created {escape(x['id'])}: <b>{escape(x['title'])}</b> → {escape(x['owner'])}"
                 for x in result["created"]]
        lines.extend(f"Needs your review: <b>{escape(x['title'])}</b> — {escape('; '.join(x['reasons']))}"
                     for x in result["needs_review"])
        await update.effective_message.reply_html(
            "Already ingested; no duplicate tasks created." if result["duplicate"] else
            ("\n".join(lines) if lines else "No high-confidence explicitly assigned tasks found. No tasks created."))
        for row in result["created"]:
            task = s.get(Task, row["id"])
            owner = s.get(Member, task.owner_id) if task else None
            if owner:
                note = service.out(owner.id, f"📌 PRD assignment: <b>{escape(row['title'])}</b> (task {escape(row['id'])}).",
                                   "system", row["id"])
                await channels.deliver(s, [note])


async def on_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    caption = (update.effective_message.caption or "").strip().split(maxsplit=1)
    if caption and caption[0].split("@", 1)[0].casefold() == "/prd":
        await prd_cmd(update, ctx)


async def reviewpr_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Report allowlisted PR metadata, diffs and checks to Telegram; never write to GitHub."""
    message = update.effective_message
    with Session(engine) as s:
        member = _member(s, update)
        if member is None or member.role != "lead":
            await message.reply_text("Only a linked team lead can request PR analysis.")
            return
        allowed = {x.strip() for x in settings.github_allowed_repos.split(",") if x.strip()}
        url = ctx.args[0] if ctx.args else ""
        try:
            report = await github.review_pr(url, allowed, settings.github_token)
        except ValueError as exc:
            await message.reply_text(str(exc))
            return
        except Exception:
            log.exception("GitHub PR analysis fetch failed")
            await message.reply_text("Could not load that allowlisted PR (the repository may be private or inaccessible).")
            return
    lines = [f"<b>{escape(report['title'])}</b> · {escape(report['repository'])}#{report['number']}",
             f"<a href=\"{escape(report['url'])}\">Open PR</a>",
             f"{escape(str(report['head_branch']))} → {escape(str(report['base_branch']))} · {len(report['files'])} file(s)"]
    checks = report.get("checks") or []
    lines.append("Checks: " + (", ".join(f"{escape(str(c['name']))}: {escape(str(c['conclusion'] or c['status']))}"
                                          for c in checks[:10]) or "none reported"))
    lines.append("Read-only GitHub data only; no review, comments, or changes were submitted.")
    await message.reply_html("\n".join(lines), disable_web_page_preview=True)


async def setgroup_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/setgroup (lead, inside the team group): make this the group the agent speaks in."""
    chat = update.effective_chat
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await update.message.reply_text("Send /setgroup inside the team group.")
        return
    with Session(engine) as s:
        me = _member(s, update)
        if me is None or me.role != "lead":
            await update.message.reply_text("Only the team lead can choose the project group. Send /join first if I do not know you yet.")
            return
        if settings.telegram_group_chat_id:
            await update.message.reply_text("TELEGRAM_GROUP_CHAT_ID is set in the server's .env and takes precedence over /setgroup.")
            return
        _remember_group(s, chat, force=True)
    await update.message.reply_text("✅ This is now the project group. I'll post blockers, risks and requests here.")


async def on_bot_added(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """The moment the bot is added to a group: remember it as the project group and explain how to start."""
    cm = update.my_chat_member
    chat = cm.chat
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return
    was_in = cm.old_chat_member.status in ("member", "administrator", "creator")
    now_in = cm.new_chat_member.status in ("member", "administrator")
    if was_in or not now_in:                                        # only when the bot joins, not on later changes
        return
    with Session(engine) as s:
        saved = _remember_group(s, chat)
        kb = _picker(s)
    me = await ctx.bot.get_me()
    sees_all = cm.new_chat_member.status == "administrator" or bool(me.can_read_all_group_messages)
    lines = ["👋 <b>Project coordinator</b> here. I keep track of tasks, blockers and deadlines for this team, right in this chat.", "",
             "1. Everyone tap your name below (or send /join) so I know who is who.",
             "2. Add a task: <code>/task Build the login page @name due friday est 3h</code>",
             "3. Tell me what is blocking you in plain words and I'll find who can help."]
    if not sees_all:
        lines += ["", "⚠️ Right now I only see /commands, @mentions and replies to my messages. Make me an <b>admin</b> of this group "
                      "(or have my owner switch off privacy in BotFather) so I can read everything."]
    if not saved:
        lines += ["", "ℹ️ This is not the project group I already use. Send /setgroup here (lead only) to switch."]
    await ctx.bot.send_message(chat.id, "\n".join(lines), parse_mode="HTML", reply_markup=kb)


async def checkin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    with Session(engine) as s:
        me = _member(s, update)
        if me is None or me.role != "lead":
            await update.message.reply_text("Only the team lead can run /checkin.")
            return
        outbox = service.followups(s, force=True)
        await channels.deliver(s, outbox)
    await update.message.reply_text(f"Sent {len(outbox)} check-in(s).")


async def status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    with Session(engine) as s:
        b = service.board(s)
    lines = [f"{service.STATUS_EMOJI[t['status']]} <b>{escape(t['title'])}</b> · {escape(t['owner'])}"
             + (f"\n    ⛔ {escape(t['blocker_text'])}" if t["status"] == "Blocked" and t["blocker_text"] else "")
             + (f"\n    ⚠️ At risk: {escape(t['at_risk'])}" if t["at_risk"] else "")
             for t in b["tasks"]]
    await update.message.reply_html("<b>Project status</b>\n" + "\n".join(lines))


async def task_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/task Build the login page @soham due friday est 3h after T1  (works in the group and in DMs)"""
    with Session(engine) as s:
        me = _member(s, update)
        if me is None:
            await update.message.reply_text("Send /join first so I know who you are.")
            return
        reply, notes = service.handle_task_command(s, me, update.message.text)
        await update.message.reply_html(reply)
        await channels.deliver(s, notes)


async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Reply to my check-ins in your own words, e.g. “connected, but waiting for test credentials”.\n"
        "/join  tell me who you are (works right in the group)\n"
        "/task Build login @soham due friday est 3h after T1  add a task\n"
        "/deps  link the obvious prerequisites between tasks\n/status  project board\n/checkin  ask everyone for updates (lead)\n"
        "/setgroup  make this group the project group (lead)\n/start  connect your account privately")


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    with Session(engine) as s:
        me = _member(s, update)
        if me is None:
            if update.effective_chat.type == ChatType.PRIVATE:
                await msg.reply_text("Press /start first so I know who you are.")
            return
        reply_to = str(msg.reply_to_message.message_id) if msg.reply_to_message else None
        if update.effective_chat.type == ChatType.PRIVATE:
            outbox = await service.handle_dm(s, me, msg.text, reply_to=reply_to, ext_id=str(msg.message_id),
                                             channel="telegram")
        else:
            _remember_group(s, update.effective_chat)
            asyncio.create_task(_typing(ctx, update.effective_chat.id))
            text = resolve_mentions(s, msg.text, getattr(ctx.bot, "username", None))
            outbox = await service.handle_group(s, me, text, ext_id=str(msg.message_id), channel="telegram",
                                                reply_to=reply_to)
            await channels.deliver(s, outbox, group_reply={"member_id": me.id, "chat_id": update.effective_chat.id,
                                                           "message_id": msg.message_id})
            return
        await channels.deliver(s, outbox)


async def _typing(ctx, chat_id: int) -> None:
    """Show "typing..." while the message is being understood, so it never looks like the bot ignored it."""
    try:
        await ctx.bot.send_chat_action(chat_id, "typing")
    except Exception:
        pass


async def on_voice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == ChatType.PRIVATE:
        await update.effective_message.reply_text("Voice notes are on the roadmap. A one-line text works best for now 🙏")


async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    with Session(engine) as s:
        if q.data.startswith("iam:"):
            m = s.exec(select(Member).where(Member.key == q.data[4:])).first()
            if not m:
                return
            if m.tg_user_id and m.tg_user_id != update.effective_user.id:      # a name cannot be taken over
                await q.message.reply_text(f"{m.name} is already connected to another Telegram account.")
                return
            _link(s, m, update)
            if update.effective_chat.type == ChatType.PRIVATE:
                await q.edit_message_text(f"✅ Connected as {m.name}.")
            else:                                                              # a group picker stays usable for everybody else
                await q.edit_message_reply_markup(_picker(s))
                await q.message.reply_html(f"✅ <a href=\"tg://user?id={update.effective_user.id}\">{escape(update.effective_user.first_name or m.name)}</a>"
                                           f" is <b>{escape(m.name)}</b>.")
            return
        me = _member(s, update)
        if me is None:
            return
        outbox = await channels.handle_callback(s, me, q.data)
        try:
            await q.edit_message_reply_markup(None)          # stop double taps
        except Exception:
            pass
        await channels.deliver(s, outbox)


async def stamp_update(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    channels.stamp("telegram")
