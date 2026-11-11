"use client";

import type { PointerEvent } from "react";

export default function LiquidOrb() {
  function tilt(event: PointerEvent<HTMLDivElement>) {
    if (
      !window.matchMedia(
        "(hover: hover) and (prefers-reduced-motion: no-preference)",
      ).matches
    )
      return;
    const bounds = event.currentTarget.getBoundingClientRect();
    event.currentTarget.style.setProperty(
      "--tilt-x",
      `${((event.clientY - bounds.top) / bounds.height - 0.5) * -18}deg`,
    );
    event.currentTarget.style.setProperty(
      "--tilt-y",
      `${((event.clientX - bounds.left) / bounds.width - 0.5) * 18}deg`,
    );
  }

  return (
    <div
      className="orb-scene"
      aria-hidden="true"
      onPointerMove={tilt}
      onPointerLeave={(event) => {
        event.currentTarget.style.setProperty("--tilt-x", "0deg");
        event.currentTarget.style.setProperty("--tilt-y", "0deg");
      }}
    >
      <span className="orb-aura" />
      <div className="orb-tilt">
        <div className="liquid-orb">
          <span className="orb-ribbon ribbon-one" />
          <span className="orb-ribbon ribbon-two" />
          <span className="orb-ribbon ribbon-three" />
          <svg className="orb-spark" viewBox="0 0 48 48" fill="none">
            <path
              d="m24 7 4.8 12.2L41 24l-12.2 4.8L24 41l-4.8-12.2L7 24l12.2-4.8L24 7Z"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinejoin="round"
            />
          </svg>
        </div>
      </div>
      <span className="orb-satellite satellite-one" />
      <span className="orb-satellite satellite-two" />
      <span className="orb-star">+</span>
    </div>
  );
}
