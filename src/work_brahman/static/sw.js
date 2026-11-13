// The workbench's Service Worker: web push (webpush.py). The browser wakes it when a push comes,
// even with no tab of the workbench open; it shows the notification, and a click opens the link.
self.addEventListener("push", event => {
  let d = {};
  try { d = event.data ? event.data.json() : {}; } catch (_) { d = { title: "brahman", body: event.data ? event.data.text() : "" }; }
  event.waitUntil(self.registration.showNotification(d.title || "brahman",
    // renotify: a push about the same story (same tag) replaces the old one AND alerts again
    // (the default replaces it silently: "it worked only once", 2026-11-12)
    { body: d.body || "", tag: d.tag || undefined, renotify: !!d.tag, data: { link: d.link || "/" } }));
});
self.addEventListener("notificationclick", event => {
  event.notification.close();
  const link = (event.notification.data && event.notification.data.link) || "/";
  event.waitUntil(clients.matchAll({ type: "window", includeUncontrolled: true }).then(list => {
    for (const c of list) if (new URL(c.url).origin === self.location.origin) { c.navigate(link); return c.focus(); }
    return clients.openWindow(link);
  }));
});
