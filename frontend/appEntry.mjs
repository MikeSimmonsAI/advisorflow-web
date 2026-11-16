// app.html: the install page for EvoSys Pro Mobile (/app). Render rewrites
// /app and /app/* to this file (render.yaml), so the FIRST HTML Safari receives
// already names the EvoSys Pro manifest and EP icon. iOS reads these tags when
// a person taps Add to Home Screen; a swap done later by React is not enough.
// Same document as index.html (same bundle, same scripts); only the install
// tags differ. The legacy mobile workspace (/m) keeps index.html and its own
// /manifest.webmanifest.
export const APP_HEAD = {
  manifest: '/manifest-evosys.webmanifest',
  icon: '/icons/evosys-apple-180.png',
  theme: '#0b1a3a',
  title: 'EvoSys',
}

export function toAppHtml(html) {
  const swap = (re, to, what) => {
    if (!re.test(html)) throw new Error('app.html: could not find the ' + what + ' tag in index.html')
    html = html.replace(re, to)
  }
  swap(/<link rel="manifest" href="[^"]*">/, `<link rel="manifest" href="${APP_HEAD.manifest}">`, 'manifest')
  swap(/<link rel="apple-touch-icon" href="[^"]*">/,
       `<link rel="apple-touch-icon" sizes="180x180" href="${APP_HEAD.icon}">\n    <meta name="apple-mobile-web-app-title" content="${APP_HEAD.title}">\n    <meta name="application-name" content="${APP_HEAD.title}">`,
       'apple-touch-icon')
  swap(/<meta name="theme-color" content="[^"]*">/, `<meta name="theme-color" content="${APP_HEAD.theme}">`, 'theme-color')
  return html
}

