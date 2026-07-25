# Face Forward — Production Website

Astro 7 + Tailwind CSS v4 static site for parkfaceforward.org.

## Structure
- `src/pages/` — exactly five First Edition routes: home, evidence, manifesto, merchandise, and about
- `src/components/` — shared navigation, footer, brand mark, top-view car, and animated hero
- `src/layouts/Layout.astro` — shared head/shell
- `src/styles/global.css` — locked Asphalt & Paint design tokens, fonts, and print styles
- `src/components/AnimatedHero.astro` — autoplay Double-F hero animation (scroll replay, reduced-motion support)
- The earlier simulator and simulation whitepaper are intentionally excluded from the public First Edition site; the Evidence page summarizes the observed-study workflow instead.

## Commands
- `npm run dev` — dev server
- `npm run build` — static build to `dist/`
- `npm run preview` — preview the build

## Notes
- Manifesto page uses `window.print()` (native browser print-to-PDF) rather than a static PDF link, since no PDF export of the manifesto exists yet.
- The Evidence page&rsquo;s four-step “How the study was made” section is the concise methodology overview; no separate one-pager or working paper is promised.
