(() => {
  const script = document.currentScript;
  const key = script?.getAttribute("data-workspace");
  if (!script || !key || !/^[a-zA-Z0-9_-]{16,64}$/.test(key)) return;
  const origin = new URL(script.src).origin;
  const button = document.createElement("button");
  button.textContent = "✦  Chat with support";
  button.setAttribute("aria-label", "Open customer support");
  button.setAttribute("aria-expanded", "false");
  button.style.cssText =
    "position:fixed;bottom:24px;right:24px;z-index:2147483000;border:1px solid #b1e8ff77;border-radius:50px;padding:16px 22px;background:linear-gradient(120deg,#91f0df,#ae9dff);color:#10192f;font:600 14px system-ui;cursor:pointer;box-shadow:0 8px 35px #18213c44;";
  const frame = document.createElement("iframe");
  frame.title = "Customer support conversation";
  frame.referrerPolicy = "no-referrer";
  frame.src = `${origin}/widget/${key}?embedded=1&site_origin=${encodeURIComponent(location.origin)}`;
  frame.style.cssText =
    "position:fixed;bottom:90px;right:24px;width:min(410px,calc(100vw - 32px));height:min(680px,calc(100dvh - 120px));border:1px solid #91c8e655;border-radius:22px;box-shadow:0 15px 65px #0005;z-index:2147482999;display:none;background:#0c1426;";
  let visible = false;
  button.addEventListener("click", () => {
    visible = !visible;
    frame.style.display = visible ? "block" : "none";
    button.setAttribute("aria-expanded", String(visible));
    button.textContent = visible ? "Close support  ×" : "✦  Chat with support";
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && visible) button.click();
  });
  document.body.append(frame, button);
})();
