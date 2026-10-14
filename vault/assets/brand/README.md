# Shared visual foundations

Use `css/app.css` before `css/ui-extras.css` when extras are needed. Tokens live in
`app.css`: charcoal surfaces, red identity, readable text accents, darker action
fills, 4px spacing steps, local DM Sans/Bebas Neue fonts, and a gold focus ring.
`appearance.js` maps each existing accent preference to accessible action colors.
Keep route-specific layout in the existing page styles; use the shared tokens.

Include `_metadata.html` once inside each page's `<head>`. Each route keeps its
own title. Metadata, app CSS, extras CSS and the appearance bundle use versioned
URLs; bump the version when changing them because the core serves immutable assets.

Import `{% from '_feedback.html' import feedback %}` in a server template. Render
`{{ feedback('loading', 'Loading your library', 'Please wait.') }}` or use `empty`
and `error`. Localize the title and description in the caller, as existing pages
do. `compact=true` reduces padding. A Jinja call block adds optional actions:

```jinja
{% call feedback('error', 'Unable to load your library', 'Please try again.') %}
  <button type="button" class="button button--primary">Try again</button>
{% endcall %}
```

For client-rendered content, use the same `.feedback`, `.feedback__title`,
`.feedback__description` and `.feedback__actions` markup. Use `role="status"`
for loading/empty states and `role="alert"` for errors; decorative SVG/spinners
have `aria-hidden="true"`. Route owners wire retry actions and state transitions.
Use `.message.message--error|success|info` for inline notices, and the existing
shared toast for transient feedback. Keep visible labels and use `aria-invalid`
and `aria-describedby` on invalid controls. `.visually-hidden` is available when
a control needs an accessible label without visible text.

`mark.svg` is the editable shield/play source. Generate committed PNG and ICO
variants with `python vault/scripts/generate_brand_assets.py` using Pillow and
CairoSVG in a disposable development environment; they are not runtime packages.
The maskable variant has a full background and keeps the mark inside the central
safe area. Existing public filenames remain compatible with templates and the
service worker. SVG/16px/32px/ICO favicons, 180px Apple touch, 192px/512px app icons,
512px maskable and `icon.png` are all generated from that source.

The shared `_header.html` loads `shell.css` after page styles and `shell.js`
before the global menu scripts. Keep navigation offsets in the shell: body
padding reserves the header, mobile tabs and safe areas. A skip link focuses the
page's existing `main` (or media layout), without changing page templates.

Page owners can opt their overlays into `SVShell.open(root, {initialFocus,
trigger, onClose})` and `SVShell.close(root)`. The root must contain a labeled
`role="dialog" aria-modal="true"` panel; place it outside fixed headers. Close
is asynchronous: the history event hides the overlay, restores focus and calls
`onClose` for cleanup. The controller preserves existing inert state, contains
Tab focus, supports arrows/D-pad and dismisses the top overlay on Escape or
remote/browser Back. `SVShell.navigate(url)` removes open overlay history
entries before navigation; ordinary overlay links use this automatically.
Native inputs, selects, media controls and sliders retain their arrow keys.
Custom widgets can mark their root with `data-native-navigation` to retain
their own arrow handling, as the existing player controls do.
