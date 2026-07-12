/* Service Worker — Web Push instablack */
self.addEventListener("install", (event) => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("push", (event) => {
  let data = { title: "instablack", body: "Nova notificação", url: "/" };
  try {
    if (event.data) data = { ...data, ...event.data.json() };
  } catch (_) {}

  // tag única por evento — não substitui notificações anteriores na tela
  const tag = data.tag || ("instablack-" + Date.now() + "-" + Math.random().toString(36).slice(2, 8));

  event.waitUntil(
    self.registration.showNotification(data.title || "instablack", {
      body: data.body || "",
      icon: "/static/favicon.svg",
      badge: "/static/favicon.svg",
      tag: tag,
      renotify: true,
      requireInteraction: false,
      data: { url: data.url || "/" },
      vibrate: [120, 60, 120],
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "/";
  event.waitUntil(
    clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
      for (const c of list) {
        if ("focus" in c) {
          c.navigate(url);
          return c.focus();
        }
      }
      if (clients.openWindow) return clients.openWindow(url);
    })
  );
});
