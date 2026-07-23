"use client";

import { useEffect, useRef, useState } from "react";

const GSI_SRC = "https://accounts.google.com/gsi/client";
const CLIENT_ID = process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID || "";

/** True when Google sign-in is configured at build time — gates the button and its divider. */
export const googleEnabled = CLIENT_ID.length > 0;

type GsiId = {
  initialize: (config: { client_id: string; callback: (res: { credential?: string }) => void }) => void;
  renderButton: (el: HTMLElement, config: Record<string, unknown>) => void;
};

declare global {
  interface Window {
    google?: { accounts?: { id?: GsiId } };
  }
}

// Inject the GIS script once per page; resolves false on load failure (button stays hidden).
let gsiLoad: Promise<boolean> | null = null;
function loadGsi(): Promise<boolean> {
  if (window.google?.accounts?.id) return Promise.resolve(true);
  if (!gsiLoad) {
    gsiLoad = new Promise((resolve) => {
      const s = document.createElement("script");
      s.src = GSI_SRC;
      s.async = true;
      s.defer = true;
      s.onload = () => resolve(!!window.google?.accounts?.id);
      s.onerror = () => resolve(false);
      document.head.appendChild(s);
    });
  }
  return gsiLoad;
}

/** Google Identity Services sign-in button (dark `filled_black` to sit on the glass card).
 *  Renders nothing when NEXT_PUBLIC_GOOGLE_CLIENT_ID is unset or the GIS script fails to load. */
export function GoogleButton({ onCredential }: { onCredential: (credential: string) => void }) {
  const host = useRef<HTMLDivElement>(null);
  const cb = useRef(onCredential);
  cb.current = onCredential;
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!googleEnabled) return;
    let gone = false;
    loadGsi().then((ok) => {
      if (gone) return;
      const gsi = window.google?.accounts?.id;
      if (!ok || !gsi || !host.current) { setFailed(true); return; }
      try {
        gsi.initialize({
          client_id: CLIENT_ID,
          callback: (res) => { if (res?.credential) cb.current(res.credential); },
        });
        gsi.renderButton(host.current, {
          theme: "filled_black",
          size: "large",
          shape: "pill",
          text: "continue_with",
          logo_alignment: "left",
          width: Math.min(400, Math.max(220, host.current.offsetWidth || 400)),
        });
      } catch {
        setFailed(true);
      }
    });
    return () => { gone = true; };
  }, []);

  if (!googleEnabled || failed) return null;
  return <div ref={host} className="flex min-h-[44px] justify-center" />;
}
