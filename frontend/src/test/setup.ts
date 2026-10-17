// jsdom has no IntersectionObserver; the React Bits animations (via motion) need one. Everything counts as in view.
class InViewObserver {
  private cb: IntersectionObserverCallback
  constructor(cb: IntersectionObserverCallback) { this.cb = cb }
  observe(target: Element) {
    this.cb([{ isIntersecting: true, intersectionRatio: 1, target } as IntersectionObserverEntry], this as unknown as IntersectionObserver)
  }
  unobserve() {}
  disconnect() {}
  takeRecords() { return [] }
}
if (!('IntersectionObserver' in globalThis)) Object.assign(globalThis, { IntersectionObserver: InViewObserver })
