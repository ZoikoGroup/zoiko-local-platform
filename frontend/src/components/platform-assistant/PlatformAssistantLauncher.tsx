"use client";

import { useCallback, useEffect, useState } from "react";
import Image from "next/image";
import { usePathname } from "next/navigation";
import { getBootstrap, moduleForPath, type Bootstrap } from "@/lib/platformAssistant";
import PlatformAssistantDrawer from "./PlatformAssistantDrawer";

export default function PlatformAssistantLauncher() {
  const pathname = usePathname();
  const [bootstrap, setBootstrap] = useState<Bootstrap | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let active = true;
    getBootstrap()
      .then((value) => {
        if (active) setBootstrap(value.enabled ? value : null);
      })
      .catch(() => {
        if (active) setBootstrap(null);
      });
    return () => {
      active = false;
    };
  }, []);

  const close = useCallback(() => setOpen(false), []);

  if (!bootstrap) return null;

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label={`Open ${bootstrap.assistant_name}`}
        aria-haspopup="dialog"
        aria-controls="platform-assistant-panel"
        title={`Open ${bootstrap.assistant_name}`}
        className="fixed bottom-5 right-5 z-40 flex min-h-[44px] items-center gap-2 rounded-full bg-slate-900 px-3 py-1.5 text-sm font-medium text-white shadow-lg focus:outline-none focus-visible:ring-2 focus-visible:ring-slate-900 focus-visible:ring-offset-2"
      >
        <Image src="/logo-icon.svg" alt="" width={28} height={28} unoptimized className="h-7 w-7 rounded-lg bg-white p-0.5" />
        <span>Assistant</span>
      </button>
      {open && (
        <PlatformAssistantDrawer
          bootstrap={bootstrap}
          module={moduleForPath(pathname)}
          onClose={close}
        />
      )}
    </>
  );
}
