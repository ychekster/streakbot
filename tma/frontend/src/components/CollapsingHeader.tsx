/**
 * Сворачивающийся заголовок «Привычки» в стиле iOS.
 *
 * Без скролла — крупный текст слева сверху (как в обычном экране). При скролле вниз
 * заголовок плавно и непрерывно уменьшается и по диагонали уходит из левого верхнего
 * угла в центр по горизонтали, опускаясь на уровень прямо под вырезом/Dynamic Island,
 * где фиксируется. При скролле вверх так же плавно возвращается. За заголовком —
 * размытие фона, которое исходит сверху и плавно затухает книзу (без видимой
 * границы и фоновой плашки); проявляется только ближе к зафиксированному состоянию.
 *
 * Плавность: анимация привязана к позиции скролла и обновляется в непрерывном цикле
 * requestAnimationFrame, который каждый кадр читает актуальный `scrollY`. Это убирает
 * рывки на iOS, где события `scroll` во время инерционной прокрутки приходят редко.
 */

import { useEffect, useRef } from "react";

import styles from "./CollapsingHeader.module.css";

interface CollapsingHeaderProps {
  title: string;
}

/** Прочитать числовое значение CSS-переменной (в px); 0, если не задана. */
function readPxVar(name: string): number {
  const raw = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const value = Number.parseFloat(raw);
  return Number.isFinite(value) ? value : 0;
}

function clamp01(value: number): number {
  return value < 0 ? 0 : value > 1 ? 1 : value;
}

export function CollapsingHeader({ title }: CollapsingHeaderProps) {
  const barRef = useRef<HTMLDivElement>(null);
  const titleRef = useRef<HTMLHeadingElement>(null);
  const spacerRef = useRef<HTMLDivElement>(null);
  // Геометрия перехода: считается при монтировании/ресайзе/смене безопасных зон,
  // на скролле только применяется (без чтения layout — чтобы не было «рывков»).
  const transition = useRef({ collapseDistance: 1, deltaX: 0, deltaY: 0, scale: 0.5 });

  useEffect(() => {
    const barEl = barRef.current;
    const titleEl = titleRef.current;
    const spacerEl = spacerRef.current;
    if (!barEl || !titleEl || !spacerEl) {
      return;
    }

    const applyAtScroll = (scrollY: number): void => {
      const { collapseDistance, deltaX, deltaY, scale } = transition.current;
      const progress = clamp01(scrollY / collapseDistance);
      const currentScale = 1 + (scale - 1) * progress;
      // translate3d держит заголовок на отдельном GPU-слое — движение плавное.
      titleEl.style.transform =
        `translate3d(${deltaX * progress}px, ${deltaY * progress}px, 0) scale(${currentScale})`;
      // Размытие появляется только ближе к зафиксированному состоянию (последняя треть).
      barEl.style.opacity = String(clamp01((progress - 0.65) / 0.35));
    };

    const recompute = (): void => {
      const safeTop = readPxVar("--app-safe-area-top");
      const contentTop = readPxVar("--app-content-safe-area-top");
      const rowHeight = readPxVar("--header-collapsed-row-height");
      const fadeTail = readPxVar("--header-fade-tail");
      const titleBottom = readPxVar("--space-title-bottom");
      const expandedFont = readPxVar("--font-size-title");
      const collapsedFont = readPxVar("--font-size-title-collapsed");

      // Зона полного размытия: безопасная зона + строка заголовка; ниже — плавное
      // затухание длиной fadeTail, поэтому у размытия нет чёткой нижней границы.
      const rowBand = contentTop > 0 ? contentTop : rowHeight;
      const solidHeight = safeTop + rowBand;
      const barHeight = solidHeight + fadeTail;
      barEl.style.height = `${barHeight}px`;
      const solidStop = Math.max(0, Math.min(100, (solidHeight / barHeight) * 100)).toFixed(1);
      const fadeMask = `linear-gradient(to bottom, #000 0%, #000 ${solidStop}%, transparent 100%)`;
      barEl.style.setProperty("mask-image", fadeMask);
      barEl.style.setProperty("-webkit-mask-image", fadeMask);

      // Натуральные позиция и размер заголовка измеряем без трансформации.
      titleEl.style.transform = "none";
      const rect = titleEl.getBoundingClientRect();
      const expandedTop = rect.top;
      const expandedLeft = rect.left;
      spacerEl.style.height = `${rect.height + titleBottom}px`;

      const scale = expandedFont > 0 ? collapsedFont / expandedFont : 0.5;
      // Свёрнутое состояние: по центру по горизонтали, прямо под вырезом/Dynamic Island.
      const collapsedCenterY = safeTop + (contentTop > 0 ? contentTop / 2 : rowHeight / 2);
      const collapsedTop = collapsedCenterY - (rect.height * scale) / 2;
      const collapsedLeft = (window.innerWidth - rect.width * scale) / 2;

      transition.current = {
        scale,
        deltaX: collapsedLeft - expandedLeft,
        deltaY: collapsedTop - expandedTop,
        collapseDistance: Math.max(1, expandedTop - collapsedTop),
      };
      applyAtScroll(window.scrollY);
    };

    // Непрерывный rAF-цикл: пока идёт скролл, каждый кадр читаем актуальный scrollY и
    // применяем трансформацию. Цикл сам останавливается, когда позиция перестаёт
    // меняться, и перезапускается по новому событию scroll — плавно и без лишней нагрузки.
    let rafId = 0;
    let lastScrollY = Number.NaN;
    let idleFrames = 0;
    const tick = (): void => {
      const y = window.scrollY;
      if (y !== lastScrollY) {
        lastScrollY = y;
        idleFrames = 0;
        applyAtScroll(y);
      } else {
        idleFrames += 1;
      }
      rafId = idleFrames < 12 ? window.requestAnimationFrame(tick) : 0;
    };
    const startLoop = (): void => {
      if (!rafId) {
        lastScrollY = Number.NaN;
        idleFrames = 0;
        rafId = window.requestAnimationFrame(tick);
      }
    };

    recompute();
    window.addEventListener("scroll", startLoop, { passive: true });
    window.addEventListener("resize", recompute);
    window.addEventListener("app:insets", recompute);
    return () => {
      if (rafId) {
        window.cancelAnimationFrame(rafId);
      }
      window.removeEventListener("scroll", startLoop);
      window.removeEventListener("resize", recompute);
      window.removeEventListener("app:insets", recompute);
    };
  }, [title]);

  return (
    <>
      <div ref={barRef} className={styles.bar} aria-hidden="true" />
      <h1 ref={titleRef} className={styles.title}>
        {title}
      </h1>
      <div ref={spacerRef} className={styles.spacer} aria-hidden="true" />
    </>
  );
}
