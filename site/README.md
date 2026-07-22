# Face Forward — Production Website

Astro 7 + Tailwind CSS v4 static site for parkfaceforward.org.

## Structure
- `src/pages/` — route pages (home, manifesto, articles, visualizer, research, get-involved, store, about, press, faq)
- `src/content/articles/` — markdown articles (content collection)
- `src/components/` — NavBar, SiteFooter
- `src/layouts/Layout.astro` — shared head/shell
- `src/styles/global.css` — design tokens (forest/emerald/chalk/gold/slate), fonts, print styles
- `public/assets/hero-animation.html` — autoplay Double-F hero animation (iframe embed, loops, respects prefers-reduced-motion)
- `public/visualizer/embed/` — the p5.js Maneuver Observatory dashboard (iframe embed for /visualizer/)
- `public/visualizer/vehicle.js`, `public/visualizer/canonical_paths.json` — simulation data/renderer, ported from `web/`
- `public/docs/methodology.pdf` — whitepaper

## Commands
- `npm run dev` — dev server
- `npm run build` — static build to `dist/`
- `npm run preview` — preview the build

## Notes
- Visualizer URL state: `/visualizer/?aisle=6.35&ped=0.18&suv=40&speed=1.0` forwards params into the embed iframe; the embed posts state changes back via `postMessage` so the parent page URL stays in sync. Permalink button copies `window.location.href`.
- Manifesto page uses `window.print()` (native browser print-to-PDF) rather than a static PDF link, since no PDF export of the manifesto exists yet.
- Store, Get Involved (pledge/story), and newsletter forms point to placeholder third-party endpoints (Buttondown, Printful, Google Forms) — swap in real account URLs before launch.
- Press kit and Chapter Starter Kit downloads reference `/docs/press-kit.zip`, `/docs/one-pager.pdf`, `/docs/chapter-starter-kit.pdf` which do not exist yet — need real assets from brand/writer workstreams before these links go live.
