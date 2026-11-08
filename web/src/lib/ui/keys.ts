// Keyboard shortcuts as the viewer's system writes them: symbols on Apple devices, "Ctrl+…" elsewhere.

const platform = (navigator as Navigator & { userAgentData?: { platform?: string } }).userAgentData?.platform || navigator.platform || navigator.userAgent;

export const isApple = /mac|iphone|ipad|ipod/i.test(platform);

/** the shortcut for the developer tools of the browser */
export const devtoolsKeys = isApple ? '⌥⌘I' : 'F12';

/** moving a row with the keyboard (Option/Alt + arrow keys) */
export const moveKeys = isApple ? '⌥↑/⌥↓' : 'Alt+↑/Alt+↓';
