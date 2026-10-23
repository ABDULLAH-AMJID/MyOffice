// MyOffice Touch Physics & Multi-Touch Gesture Engine (gestures.js)
// Implements:
//  1. 60fps 2-finger focal-point pinch-to-zoom (zero zoom drift)
//  2. Double-tap smart zoom toggle anchored at exact finger coordinates
//  3. Smooth snap-back to 1.0x fit-width when near 100%
//  4. Horizontal swipe detection (slides / presenter)
//  5. Native-feel touch drag-to-dismiss on bottom sheets
(function () {
  function dist(t0, t1) {
    return Math.hypot(t1.clientX - t0.clientX, t1.clientY - t0.clientY);
  }

  function midpoint(t0, t1, rect) {
    return {
      x: (t0.clientX + t1.clientX) / 2 - rect.left,
      y: (t0.clientY + t1.clientY) / 2 - rect.top
    };
  }

  /**
   * Attaches viewer-owned focal pinch-to-zoom + double-tap zoom to a scroll container
   * without zooming surrounding toolbars or UI chrome.
   */
  function attachZoomSurface(viewportEl, contentEl, opts = {}) {
    if (!viewportEl || !contentEl) return null;
    const minScale = opts.minScale ?? 1.0;
    const maxScale = opts.maxScale ?? 4.25;
    const doubleTapScale = opts.doubleTapScale ?? 2.25;
    let scale = opts.initialScale ?? 1.0;
    let baseWidth = 0;
    let pinching = false;
    let startDist = 0;
    let startScale = 1.0;
    let settleTimer = null;
    let badgeTimer = null;
    let lastTapTime = 0;
    let lastTapX = 0;
    let lastTapY = 0;
    let downX = 0;
    let downY = 0;
    let downTime = 0;
    let multiTouchSeen = false;

    // Floating transient zoom indicator capsule (tap to reset to 100%)
    let badge = viewportEl.parentElement?.querySelector(".zoom-hud-pill");
    if (!badge && viewportEl.parentElement) {
      badge = document.createElement("button");
      badge.type = "button";
      badge.className = "zoom-hud-pill hidden";
      badge.onclick = (e) => {
        e.stopPropagation();
        api.setScale(1.0, viewportEl.clientWidth / 2, viewportEl.clientHeight / 2, true);
      };
      viewportEl.parentElement.appendChild(badge);
    }

    function showZoomHud() {
      if (!badge) return;
      badge.textContent = Math.round(scale * 100) + "%";
      badge.classList.remove("hidden");
      clearTimeout(badgeTimer);
      badgeTimer = setTimeout(() => {
        if (Math.abs(scale - 1.0) < 0.03) badge.classList.add("hidden");
      }, 1100);
      if (Math.abs(scale - 1.0) < 0.03) {
        clearTimeout(badgeTimer);
        badgeTimer = setTimeout(() => badge.classList.add("hidden"), 650);
      }
    }

    function ensureBaseWidth() {
      if (!baseWidth || scale === 1.0) {
        baseWidth = contentEl.offsetWidth / (scale || 1.0);
      }
      if (!baseWidth) baseWidth = viewportEl.clientWidth;
    }

    function applyScaleAtFocal(nextScale, focalX, focalY, settled = false) {
      ensureBaseWidth();
      const clamped = Math.max(minScale, Math.min(maxScale, nextScale));
      const ratio = clamped / scale;
      if (Math.abs(ratio - 1) < 0.0005 && !settled) return;

      const prevScrollLeft = viewportEl.scrollLeft;
      const prevScrollTop = viewportEl.scrollTop;

      scale = clamped;
      if (opts.mode === "cssZoom") {
        contentEl.style.zoom = String(scale);
      } else {
        contentEl.style.width = Math.max(viewportEl.clientWidth - 16, Math.round(baseWidth * scale)) + "px";
      }

      // Preserve exact document coordinate beneath focal point
      viewportEl.scrollLeft = Math.max(0, (prevScrollLeft + focalX) * ratio - focalX);
      viewportEl.scrollTop = Math.max(0, (prevScrollTop + focalY) * ratio - focalY);

      showZoomHud();
      if (opts.onScaleFrame) opts.onScaleFrame(scale);

      clearTimeout(settleTimer);
      if (settled) {
        if (opts.onScaleSettled) opts.onScaleSettled(scale);
      } else {
        settleTimer = setTimeout(() => {
          if (opts.onScaleSettled) opts.onScaleSettled(scale);
        }, 190);
      }
    }

    viewportEl.addEventListener("touchstart", (e) => {
      if (e.touches.length === 1) {
        downX = e.touches[0].clientX;
        downY = e.touches[0].clientY;
        downTime = Date.now();
        multiTouchSeen = false;
      } else if (e.touches.length === 2) {
        multiTouchSeen = true;
        pinching = true;
        clearTimeout(viewportEl._singleTapTimer);
        ensureBaseWidth();
        startDist = Math.max(12, dist(e.touches[0], e.touches[1]));
        startScale = scale;
      }
    }, { passive: true });

    viewportEl.addEventListener("touchmove", (e) => {
      if (e.touches.length === 2 && pinching) {
        e.preventDefault();
        const rect = viewportEl.getBoundingClientRect();
        const curDist = Math.max(12, dist(e.touches[0], e.touches[1]));
        const mid = midpoint(e.touches[0], e.touches[1], rect);
        applyScaleAtFocal(startScale * (curDist / startDist), mid.x, mid.y, false);
      }
    }, { passive: false });

    viewportEl.addEventListener("touchend", (e) => {
      if (pinching && e.touches.length < 2) {
        pinching = false;
        // Snap cleanly to fit-width if user pinched within 6% of 100%
        if (scale > 0.94 && scale < 1.06) {
          applyScaleAtFocal(1.0, viewportEl.clientWidth / 2, viewportEl.clientHeight / 2, true);
        } else if (opts.onScaleSettled) {
          clearTimeout(settleTimer);
          opts.onScaleSettled(scale);
        }
        return;
      }

      // Single-finger tap / double-tap smart zoom ONLY if finger did not scroll/pan (< 12px & < 280ms)
      if (e.changedTouches.length === 1 && !pinching && !multiTouchSeen) {
        const t = e.changedTouches[0];
        const now = Date.now();
        const dragDist = Math.hypot(t.clientX - downX, t.clientY - downY);
        const holdMs = now - downTime;
        if (dragDist > 12 || holdMs > 280) {
          return;
        }
        const tapPairDist = Math.hypot(t.clientX - lastTapX, t.clientY - lastTapY);
        if (now - lastTapTime < 300 && tapPairDist < 34) {
          e.preventDefault();
          lastTapTime = 0;
          clearTimeout(viewportEl._singleTapTimer);
          const rect = viewportEl.getBoundingClientRect();
          const fx = t.clientX - rect.left;
          const fy = t.clientY - rect.top;
          const target = scale > 1.15 ? 1.0 : doubleTapScale;
          applyScaleAtFocal(target, fx, fy, true);
        } else {
          lastTapTime = now;
          lastTapX = t.clientX;
          lastTapY = t.clientY;
          if (opts.onSingleTap) {
            clearTimeout(viewportEl._singleTapTimer);
            viewportEl._singleTapTimer = setTimeout(() => {
              const tag = (e.target && e.target.tagName) || "";
              if (!["BUTTON", "INPUT", "TEXTAREA", "SELECT", "A"].includes(tag)) {
                opts.onSingleTap(e);
              }
            }, 305);
          }
        }
      }
    }, { passive: false });

    // Support Ctrl+Wheel / trackpad pinch for hybrid keyboard/mouse & automated verification
    viewportEl.addEventListener("wheel", (e) => {
      if (e.ctrlKey || e.metaKey) {
        e.preventDefault();
        const rect = viewportEl.getBoundingClientRect();
        const factor = e.deltaY < 0 ? 1.12 : 0.89;
        applyScaleAtFocal(scale * factor, e.clientX - rect.left, e.clientY - rect.top, false);
      }
    }, { passive: false });

    const api = {
      getScale: () => scale,
      setScale: (next, fx, fy, settled = true) =>
        applyScaleAtFocal(next, fx ?? viewportEl.clientWidth / 2, fy ?? viewportEl.clientHeight / 2, settled)
    };
    return api;
  }

  /**
   * Attaches horizontal left/right swipe navigation (e.g., Slides & Presenter).
   */
  function attachHorizontalSwipe(el, onPrev, onNext) {
    if (!el) return;
    let sx = 0, sy = 0, tracking = false;
    el.addEventListener("touchstart", (e) => {
      if (e.touches.length !== 1) { tracking = false; return; }
      sx = e.touches[0].clientX;
      sy = e.touches[0].clientY;
      tracking = true;
    }, { passive: true });
    el.addEventListener("touchend", (e) => {
      if (!tracking || e.changedTouches.length !== 1) return;
      tracking = false;
      const dx = e.changedTouches[0].clientX - sx;
      const dy = e.changedTouches[0].clientY - sy;
      if (Math.abs(dx) > 54 && Math.abs(dx) > Math.abs(dy) * 1.6) {
        if (dx < 0) onNext && onNext();
        else onPrev && onPrev();
      }
    }, { passive: true });
  }

  /**
   * Enables swipe-down gesture on any bottom sheet to dismiss naturally like native Android sheets.
   */
  function attachBottomSheetSwipeClose(sheetEl, onDismiss) {
    if (!sheetEl) return;
    let startY = 0;
    let deltaY = 0;
    let dragging = false;

    sheetEl.addEventListener("touchstart", (e) => {
      if (e.touches.length !== 1) return;
      if (sheetEl.scrollTop > 4) return;
      startY = e.touches[0].clientY;
      deltaY = 0;
      dragging = true;
      sheetEl.style.transition = "none";
    }, { passive: true });

    sheetEl.addEventListener("touchmove", (e) => {
      if (!dragging) return;
      const dy = e.touches[0].clientY - startY;
      if (dy > 0) {
        deltaY = dy;
        sheetEl.style.transform = `translateY(${Math.min(dy, 360)}px)`;
      }
    }, { passive: true });

    sheetEl.addEventListener("touchend", () => {
      if (!dragging) return;
      dragging = false;
      sheetEl.style.transition = "transform 0.2s cubic-bezier(0.2, 0.85, 0.3, 1)";
      if (deltaY > 88) {
        sheetEl.style.transform = "translateY(100%)";
        setTimeout(() => {
          sheetEl.style.transform = "";
          onDismiss && onDismiss();
        }, 160);
      } else {
        sheetEl.style.transform = "translateY(0)";
      }
    }, { passive: true });
  }

  window.MyOfficeGestures = {
    attachZoomSurface,
    attachHorizontalSwipe,
    attachBottomSheetSwipeClose
  };
})();
