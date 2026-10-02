// Service worker: maakt de app installeerbaar en houdt laatst bekeken content
// (dia's, documentmetadata, de markdown/KaTeX-libraries) ook zonder internet
// bekijkbaar. Genereert géén AI-uitleg offline — dat kan sowieso niet zonder
// netwerk, en wordt nergens in de UI beloofd.
const CACHE_VERSION = "sc-cache-v48";

const APP_SHELL = [
  "./", "./index.html", "./manifest.json",
  "./css/app.css",
  "./icons/icon-192.png", "./icons/icon-512.png",
  "./branding/favicon.svg", "./branding/favicon-32.png",
  "./branding/studygrasp-symbol.svg",
  // Lokaal gevendorde libraries (markdown/sanitizer/wiskunde) + fonts — meteen
  // offline beschikbaar, geen CDN nodig.
  "./vendor/marked.min.js", "./vendor/purify.min.js",
  "./vendor/katex.min.js", "./vendor/katex.min.css",
  "./vendor/chart.umd.min.js",
  "./vendor/fonts.css",
  "./vendor/fonts-ui/inter-latin-400-normal.woff2", "./vendor/fonts-ui/inter-latin-500-normal.woff2",
  "./vendor/fonts-ui/inter-latin-600-normal.woff2", "./vendor/fonts-ui/inter-latin-700-normal.woff2",
  "./vendor/fonts-ui/inter-latin-800-normal.woff2",
  "./vendor/fonts-ui/jetbrains-mono-latin-400-normal.woff2", "./vendor/fonts-ui/jetbrains-mono-latin-600-normal.woff2",
  "./js/app.js", "./js/api.js", "./js/config.js", "./js/export.js", "./js/i18n.js",
  "./js/markdown.js", "./js/charts.js", "./js/plot.js", "./js/review.js",
  "./js/search.js", "./js/state.js", "./js/stats.js", "./js/tts.js", "./js/util.js", "./js/recovery.js",
  "./js/views/exam.js", "./js/views/flashcards.js", "./js/views/folder.js", "./js/views/home.js",
  "./js/views/quiz.js", "./js/views/study.js", "./js/views/summary.js", "./js/views/privacy.js",
  "./js/views/quick.js", "./js/views/wordlist.js", "./js/views/exercises.js", "./js/views/billing.js",
  "./js/views/login.js",
];

// De KaTeX-woff2-fonts (same-origin, vendor/fonts/) worden bij eerste gebruik
// gecachet door de origin-eerst-handler. Er zijn geen externe hosts meer:
// alle fonts en libraries zijn lokaal gevendord.
const RUNTIME_HOSTS = [];

function isRuntimeCacheable(url) {
  // Accountgebonden documenten en dia's worden bewust niet door de service
  // worker bewaard: Cache API varieert niet betrouwbaar per Authorization-
  // header. De gewone privécache van de browser blijft wel beschikbaar.
  return RUNTIME_HOSTS.includes(url.hostname);
}

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_VERSION).then((cache) => cache.addAll(APP_SHELL)).then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE_VERSION).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;
  const url = new URL(request.url);

  if (url.origin === self.location.origin) {
    // Netwerk-eerst: tijdens ontwikkeling wil je altijd de nieuwste JS/CSS (de
    // dev-server stuurt daarom ook al no-cache-headers naar de browser). Pas
    // bij een mislukte fetch (écht offline) valt dit terug op de laatst
    // gecachete versie, zodat de eerdere no-cache-fix niet wordt omzeild.
    // Code (js/css) is tot nu toe zonder Cache-Control uitgeleverd, dus browsers
    // mochten er heuristisch een eigen bewaartermijn op plakken. Daardoor kan een
    // oude bundel blijven hangen ook al staat er een nieuwe klaar. "no-cache"
    // dwingt revalidatie af (met ETag kost dat alleen een 304, geen body).
    const isCode = /^\/(js|css)\//.test(url.pathname);
    const req = isCode ? new Request(request, { cache: "no-cache" }) : request;
    event.respondWith(
      fetch(req)
        .then((resp) => {
          const copy = resp.clone();
          caches.open(CACHE_VERSION).then((cache) => cache.put(request, copy));
          return resp;
        })
        .catch(() => caches.match(request)),
    );
    return;
  }

  if (isRuntimeCacheable(url)) {
    // Stale-while-revalidate: toon meteen de cache (indien aanwezig) en
    // ververs op de achtergrond.
    event.respondWith(
      caches.open(CACHE_VERSION).then(async (cache) => {
        const cached = await cache.match(request);
        const network = fetch(request)
          .then((resp) => { if (resp.ok) cache.put(request, resp.clone()); return resp; })
          .catch(() => cached);
        return cached || network;
      }),
    );
  }
});
