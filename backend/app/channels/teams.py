"""Microsoft Teams via the Teams SDK for Python (microsoft-teams-apps, GA May 2026).

The SDK mounts its messaging endpoint (/api/teams/messages) on our own FastAPI app through
FastAPIAdapter and validates Bot Framework JWTs itself. Register an Azure Bot (Entra app ID
+ secret) pointing at https://<your-domain>/api/teams/messages and sideload the Teams app
manifest in deploy/teams-manifest/.

Personal chats map to handle_dm; channel or group chats map to handle_group. Buttons are
Adaptive Card Action.Submit actions carrying {"coord": "<callback>"}, which Teams returns
as the message activity's `value`.
"""
import logging
import re

from sqlmodel import Session, select

from .. import channels, service
from ..config import settings
from ..db import engine
from ..models import Member

log = logging.getLogger("teams")


def card_for(item: dict):
    from microsoft_teams.cards import AdaptiveCard, SubmitAction, TextBlock

    actions = [SubmitAction(title=label, data={"coord": data})
               for row in (item.get("buttons") or []) for label, data in row]
    return AdaptiveCard(body=[TextBlock(text=channels.to_markdown(item["text"]), wrap=True)], actions=actions or None)


class TeamsChannel:
    name = "teams"

    def __init__(self):
        self.app = None
        self.initialized = False

    def ready(self) -> bool:
        return self.initialized

    def linked(self, m: Member) -> bool:
        return bool(m.teams_conv_id)

    async def send(self, m: Member, item: dict) -> str | None:
        payload = card_for(item) if item.get("buttons") else channels.to_markdown(item["text"])
        sent = await self.app.send(m.teams_conv_id, payload, service_url=m.teams_service_url)
        return sent.id

    def build(self, fastapi_app=None) -> None:
        """Create the SDK app. With a FastAPI app, its messaging route is mounted there."""
        if not settings.teams_enabled or self.app is not None:
            return
        from microsoft_teams.apps import App
        from microsoft_teams.apps.http.fastapi_adapter import FastAPIAdapter

        self.app = App(client_id=settings.teams_client_id, client_secret=settings.teams_client_secret,
                       tenant_id=settings.teams_tenant_id or None,
                       http_server_adapter=FastAPIAdapter(app=fastapi_app) if fastapi_app else FastAPIAdapter(),
                       messaging_endpoint="/api/teams/messages")
        _register(self.app)

    async def start(self) -> None:
        if self.app is None:
            return
        await self.app.initialize()          # registers the route and the token manager; no extra server
        self.initialized = True
        log.info("Teams ready at /api/teams/messages")


CHANNEL = TeamsChannel()
channels.register(CHANNEL)


def _resolve(s: Session, activity) -> Member | None:
    frm = activity.from_
    m = s.exec(select(Member).where(Member.teams_aad_id == frm.aad_object_id)).first() if frm.aad_object_id else None
    if m is None and getattr(frm, "email", None):
        m = s.exec(select(Member).where(Member.email == frm.email.lower())).first()
    if m is None and frm.name:                              # display names match the roster in most college teams
        m = s.exec(select(Member).where(Member.name == frm.name.split()[0])).first()
    if m is not None and activity.conversation.conversation_type in (None, "personal"):
        if m.teams_conv_id != activity.conversation.id or m.teams_aad_id != frm.aad_object_id:
            channels.link(s, m, "teams", teams_aad_id=frm.aad_object_id, teams_conv_id=activity.conversation.id,
                          teams_service_url=activity.service_url)
    return m


def _register(app) -> None:
    from microsoft_teams.api import InstalledActivity, MessageActivity
    from microsoft_teams.apps import ActivityContext

    @app.on_install_add
    async def on_install(ctx: ActivityContext[InstalledActivity]):
        with Session(engine) as s:
            m = _resolve(s, ctx.activity)
        await ctx.send(f"Hi {m.name}! I'll check in about your tasks here." if m else
                       "Hi! Ask your team lead to add your email to the roster so I can link your account.")

    @app.on_message
    async def on_message(ctx: ActivityContext[MessageActivity]):
        a = ctx.activity
        with Session(engine) as s:
            me = _resolve(s, a)
            if me is None:
                await ctx.send("I don't know you yet. Ask your team lead to add your email to the roster.")
                return
            value = a.value if isinstance(a.value, dict) else {}
            if value.get("coord"):                                   # Adaptive Card button
                outbox = await channels.handle_callback(s, me, value["coord"])
            else:
                text = re.sub(r"<at>.*?</at>", "", a.text or "").strip()
                text = re.sub(r"<[^>]+>", "", text)
                if a.conversation.conversation_type in (None, "personal"):
                    outbox = await service.handle_dm(s, me, text, reply_to=a.reply_to_id, ext_id=a.id, channel="teams")
                else:
                    outbox = await service.handle_group(s, me, text, ext_id=a.id, channel="teams")
            await channels.deliver(s, outbox)
