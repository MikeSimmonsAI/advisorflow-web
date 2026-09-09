# GOD-04 Patch Instructions

## File 1: frontend/src/pages/god/GodRoadmapBoard.jsx
→ NEW FILE — copy GodRoadmapBoard.jsx from this delivery

## File 2: frontend/src/App.jsx
Add import (with the other god page imports):
```jsx
import GodRoadmapBoard from './pages/god/GodRoadmapBoard'
```

Add route BEFORE the `/god/*` catch-all:
```jsx
{/* GOD-04: platform roadmap board */}
<Route path="/god/roadmap" element={<GodRoute><GodModeLayout><GodRoadmapBoard /></GodModeLayout></GodRoute>} />
```

## File 3: frontend/src/pages/GodShell.jsx
Add nav entry in the OPERATIONS or PLATFORM group (wherever ProductStatus lives):
```js
{ label: 'Platform Roadmap', path: '/god/roadmap', icon: 'map',
  hint: '93 capability items across 15 systems — what is complete, what needs finishing' },
```

No backend changes. No new endpoints. The component imports
frontend/src/data/platformRoadmap.json statically.
