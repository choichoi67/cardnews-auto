# Cardnews Control Room Design System

## Product definition

- Type: editorial automation operations dashboard
- Core flow: collect → review → approve → AI build → Drive → Telegram
- Style: Soft UI Evolution + restrained Liquid Glass
- Priority: usability 40%, accessibility/readability 30%, reference accuracy 25%, motion 5%

## Tokens

| Role | Value |
|---|---|
| Background | `#F4F1F8` |
| Glass surface | `rgba(255,255,255,.76)` |
| Solid surface | `#FCFBFE` |
| Foreground | `#171725` |
| Muted foreground | `#666477` |
| Border | `#E3DDEA` |
| Primary | `#7C3AED` |
| Secondary | `#4F7CFF` |
| Accent | `#F65DB1` |
| Success | `#157F56` |
| Warning | `#A15C00` |
| Destructive | `#C93652` |

- Font: `Manrope`, `Noto Sans KR`, system sans-serif
- Radius: input 14px, button 15px, panel 22px, glass frame 30px
- Blur: 24px only for navigation, workflow summary and modal
- Motion: 150–250ms, `cubic-bezier(.2,.8,.2,1)`
- Focus: 2px primary outline + 3px translucent ring

## Rules

- Keep operational content panels highly opaque; do not stack blurred surfaces.
- Use gradients only for primary actions, active automation states and small accents.
- Maintain 4.5:1 contrast for normal text and 3:1 for large text/icons.
- Every interactive target is at least 44×44px and has a visible focus state.
- Never communicate status by color alone; pair color with Korean status text.
- Use inline SVG icons with consistent 1.8–2px strokes.
- Respect `prefers-reduced-motion` and avoid layout-shifting animation.
- On mobile, preserve channel names and creation controls in a horizontal channel rail.

## Reference accuracy checklist

- Warm milk-white background
- High-contrast black typography
- 30/22/15px radius hierarchy
- Diffused cool-purple shadows
- White top-edge glass highlight
- Restricted violet/pink/cyan gradients
- Generous section spacing with dense content kept inside cards

