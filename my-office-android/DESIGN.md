# MyOffice Mobile Design System (`DESIGN.md`)

## 1. Committed Aesthetic Direction: Swiss Archival Paper & Editorial Studio
MyOffice Mobile rejects AI-default SaaS tropes in favor of tactile document stationery inspired by archival paper, Swiss typographic grids, and mobile ergonomics (Fitts's Law & Android 15 `targetSdk=35` inset discipline).

## 2. Explicit Anti-Patterns Strictly Prohibited (`0` Allowed Instances)
1. **No multi-stop or purple/indigo/teal gradients** anywhere on headers, cards, buttons, or text.
2. **No in-app marketing hero banners** advertising features the user already installed. Documents appear immediately above the fold.
3. **No Unicode emojis used as UI icons** (`📂`, `📷`, `✨`, `🏠`, `🔒`, etc.). All icons use a single unified `1.75px` stroke geometric SVG set (`currentColor`).
4. **No 4-point sparkle glyph (`✨`)** for AI features.
5. **No Android 15 status bar, punch-hole cutout, bottom gesture bar, or keyboard overlap**: `MainActivity` consumes `WindowInsetsCompat.Type.systemBars() | displayCutout() | ime()` natively and synchronizes status/navigation bar icon contrast on theme change.
6. **No cramped micro-buttons in top corners**: every touch target is `>= 44x44dp`; Language (`English` / `اردو` / `हिन्दी`) and Appearance (`Paper Light` / `Carbon Dark`) live in a thumb-reachable Preferences Bottom Sheet with `48dp` segmented controls.
7. **No nested cards inside cards ("cardocalypse")** or colored `4px` side-stripe borders: workspace items use clean architectural hairline dividers inside a single elevated paper sheet.
8. **No browser `window.prompt()` / `window.confirm()` dialogs**: all prompts and confirmations use custom bottom-sheet dialogs.

## 3. Token Specification
- **Daylight Paper (`[data-theme="light"]`, default)**:
  - Canvas `#F4F3EE`, Surface `#FFFFFF`, Subtle Well `#ECEAE3`, Sumi Ink `#191918`, Secondary Ink `#575652`, Brand Accent `#1E40AF` (Deep Cobalt).
- **Carbon Studio (`[data-theme="dark"]`)**:
  - Canvas `#161615`, Surface `#20201E`, Subtle Well `#2A2A27`, Ink `#F2F0EA`, Secondary Ink `#A8A59E`, Brand Accent `#60A5FA`.
- **Spacing Grid**: 4px half-step / 8px base rhythm (`8, 12, 16, 20, 24, 32px`).
- **Radius Hierarchy**: `6px` badges, `10px` inputs & buttons, `14px` sheet containers, `20px` bottom drawers.
