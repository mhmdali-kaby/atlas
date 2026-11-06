# ATL-247 — Settings → General: the Sign-in (two-factor) group

Built on ATL-245's `/api/me/two-factor` (see `ATL-245-two-factor-api.md`). Design v22 §5.2–5.3.

## Where

- `frontend/src/features/settings-general/TwoFactorSection.tsx` — the group and its sheet; mounted in
  `GeneralPage.tsx` straight after **You**. Styles in `twofactor.css`; strings under `tf.*` in
  `i18n/locales/{en,ar}/settings-general.json` (the page intro now mentions sign-in).
- Query key `['me', 'two-factor']` (GET `/api/me/two-factor`). Start/confirm/recovery-codes/DELETE write
  the returned status into that key, so the group updates without a refetch. ATL-248's post-recovery-code
  toast can read the same key.

## Shared pieces touched

- `ui/OtpField.tsx` (`OtpField`, `OtpDial`), the `.otp` block in `ui.css`, the `ui.otp.label` string and
  the `ui/index.ts` export are **DEV1's from task/ATL-248, copied byte-for-byte** so both branches add the
  same lines and merge cleanly. If ATL-248 lands first, nothing changes; if DEV1 edits OtpField later,
  that edit wins.
- `ui/Sheet.tsx` gained three optional props, no behaviour change for existing callers:
  `locked` (ignores Esc, outside clicks and onOpenChange(false)), `role` (`'alertdialog'`), `iconAside`
  (shown at the end of the icon's row, used for "Step n of 3"); and two tones, `accent` and `profit`.

## Behaviour

- Off → Turn on… → password (POST start) → QR as `<img src="data:image/svg+xml,…">` from `qr_svg`
  (never injected as HTML), setup key in groups of four (dir=ltr, `data-secret` holds the raw key), code
  cells auto-submit at 6 digits (POST confirm) → codes certificate; that step is `locked` +
  `role=alertdialog`, only "I saved them" closes it.
- On: confirmed date, eight marks + "n of 8 left", orange notice at ≤ 2, New codes… is primary at 0;
  New codes… and Turn off… each take the password. In live with 2FA off, Turn on… is primary with the
  live notice.
- Errors: 422 shows the API's field message (password or code); 429 and no answer show our own words in a
  red line above the field; a 409 shows the server message and refetches the status.

## Tests

- Vitest `TwoFactorSection.test.tsx` (mocked fetch).
- Playwright `e2e/settings-two-factor.spec.ts` computes a real RFC 6238 code from the secret on screen,
  light and dark; screenshots go to `evidence/ATL-247/`.
