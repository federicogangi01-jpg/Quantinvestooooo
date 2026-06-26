(function () {
  const ChartLite = {
    tooltip: null,
    ensureTooltip() {
      if (!this.tooltip) {
        this.tooltip = document.createElement("div");
        this.tooltip.className = "chart-tooltip";
        this.tooltip.hidden = true;
        this.tooltip.style.display = "none";
        document.body.appendChild(this.tooltip);
      }
      return this.tooltip;
    },
    hideTooltip() {
      if (this.tooltip) {
        this.tooltip.hidden = true;
        this.tooltip.innerHTML = "";
        this.tooltip.style.display = "none";
      }
    },
    clearTooltip(canvas) {
      if (canvas?._chartLiteCleanup) canvas._chartLiteCleanup();
      this.hideTooltip();
    },
    attachTooltip(canvas, points = []) {
      if (!canvas) return;
      this.clearTooltip(canvas);
      const tooltip = this.ensureTooltip();
      const validPoints = points.filter((point) => Number.isFinite(point.x) && Number.isFinite(point.y));
      const step = Math.max(1, Math.ceil(validPoints.length / 600));
      const normalized = validPoints.filter((_, index) => index % step === 0 || index === validPoints.length - 1);
      if (!normalized.length) return;
      const isOutsideCanvas = (event) => {
        if (!event) return true;
        const rect = canvas.getBoundingClientRect();
        return event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom;
      };
      const nearest = (event) => {
        if (!event) return;
        if (isOutsideCanvas(event)) {
          hide();
          return;
        }
        const rect = canvas.getBoundingClientRect();
        const scaleX = canvas.width / rect.width;
        const scaleY = canvas.height / rect.height;
        const x = (event.clientX - rect.left) * scaleX;
        const y = (event.clientY - rect.top) * scaleY;
        let best = null;
        let bestDistance = Infinity;
        normalized.forEach((point) => {
          const distance = Math.hypot(point.x - x, point.y - y);
          if (distance < bestDistance) {
            best = point;
            bestDistance = distance;
          }
        });
        if (!best || bestDistance > 42) {
          hide();
          return;
        }
        tooltip.innerHTML = best.html || window.escapeHtml?.(best.label || "") || "";
        tooltip.hidden = false;
        tooltip.style.display = "grid";
        tooltip.style.left = `${Math.min(window.innerWidth - 280, Math.max(12, event.clientX + 14))}px`;
        tooltip.style.top = `${Math.max(12, event.clientY + 14)}px`;
      };
      const hide = () => {
        tooltip.hidden = true;
        tooltip.innerHTML = "";
        tooltip.style.display = "none";
      };
      const hideIfOutside = (event) => {
        if (isOutsideCanvas(event)) hide();
      };
      const hideIfPointerTargetsOutside = (event) => {
        if (event?.target === canvas || canvas.contains(event?.target)) return;
        hide();
      };
      const hideIfLeavingWindow = (event) => {
        if (!event.relatedTarget) hide();
      };
      const hideOnVisibilityChange = () => {
        if (document.hidden) hide();
      };
      const touchStart = (event) => nearest(event.touches[0]);
      const touchMove = (event) => nearest(event.touches[0]);
      canvas.addEventListener("mousemove", nearest);
      canvas.addEventListener("pointermove", nearest);
      canvas.addEventListener("mouseleave", hide);
      canvas.addEventListener("pointerleave", hide);
      canvas.addEventListener("mouseout", hide);
      canvas.addEventListener("pointercancel", hide);
      canvas.addEventListener("blur", hide);
      canvas.addEventListener("touchstart", touchStart, { passive: true });
      canvas.addEventListener("touchmove", touchMove, { passive: true });
      canvas.addEventListener("touchend", hide);
      document.addEventListener("pointermove", hideIfOutside, true);
      document.addEventListener("pointerover", hideIfPointerTargetsOutside, true);
      document.addEventListener("pointerdown", hideIfOutside, true);
      document.addEventListener("mouseout", hideIfLeavingWindow, true);
      document.addEventListener("visibilitychange", hideOnVisibilityChange);
      document.addEventListener("scroll", hide, true);
      window.addEventListener("blur", hide);
      window.addEventListener("resize", hide);
      window.addEventListener("scroll", hide, true);
      canvas._chartLiteCleanup = () => {
        canvas.removeEventListener("mousemove", nearest);
        canvas.removeEventListener("pointermove", nearest);
        canvas.removeEventListener("mouseleave", hide);
        canvas.removeEventListener("pointerleave", hide);
        canvas.removeEventListener("mouseout", hide);
        canvas.removeEventListener("pointercancel", hide);
        canvas.removeEventListener("blur", hide);
        canvas.removeEventListener("touchstart", touchStart);
        canvas.removeEventListener("touchmove", touchMove);
        canvas.removeEventListener("touchend", hide);
        document.removeEventListener("pointermove", hideIfOutside, true);
        document.removeEventListener("pointerover", hideIfPointerTargetsOutside, true);
        document.removeEventListener("pointerdown", hideIfOutside, true);
        document.removeEventListener("mouseout", hideIfLeavingWindow, true);
        document.removeEventListener("visibilitychange", hideOnVisibilityChange);
        document.removeEventListener("scroll", hide, true);
        window.removeEventListener("blur", hide);
        window.removeEventListener("resize", hide);
        window.removeEventListener("scroll", hide, true);
        tooltip.hidden = true;
        tooltip.innerHTML = "";
        tooltip.style.display = "none";
        canvas._chartLiteCleanup = null;
      };
    },
    shortLabel(value, max = 18) {
      const label = String(value || "--");
      return label.length > max ? `${label.slice(0, max - 2)}...` : label;
    },
    impactLabel(value) {
      const percent = window.formatPercent?.(value) || `${Number(value || 0).toFixed(2)}%`;
      const capital = Number(document.querySelector("#initial-capital")?.value || 0);
      if (!capital || !Number.isFinite(value)) return percent;
      return `${percent} · ${window.euro?.format(capital * value) || capital * value}`;
    },
  };

  window.ChartLite = ChartLite;
})();
