/* Bloom app offline cache — Meridian Zero */
const PREFIX="bloom-book-";
const C=PREFIX+"v9";
const FILES=["./","index.html","config.js","manifest.webmanifest","icons/icon-192.png","icons/icon-512.png","icons/apple-touch-icon.png"];
self.addEventListener("install",e=>{e.waitUntil(caches.open(C).then(c=>c.addAll(FILES)));self.skipWaiting()});
/* only clear this app's own old caches, never other apps on the same site */
self.addEventListener("activate",e=>{e.waitUntil(caches.keys().then(k=>Promise.all(k.filter(x=>(x.startsWith(PREFIX)||/^bloom-v\d+$/.test(x))&&x!==C).map(x=>caches.delete(x)))));self.clients.claim()});
self.addEventListener("fetch",e=>{
  const u=new URL(e.request.url);
  if(e.request.method!=="GET")return;
  if(u.origin!==location.origin)return; /* live prices, bookings and other sites always go to the network */
  e.respondWith(fetch(e.request).then(r=>{
    if(r.ok&&r.type==="basic"){const cp=r.clone();caches.open(C).then(c=>c.put(e.request,cp))}
    return r;
  }).catch(()=>caches.match(e.request).then(m=>m||(e.request.mode==="navigate"?caches.match("index.html"):undefined))));
});
