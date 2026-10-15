// LBEILC Blog Theme SurfaceTransition. MIT; see SOURCES.md.
const enterEase = "cubic-bezier(0.22, 1, 0.36, 1)";
const exitEase = "cubic-bezier(0.4, 0, 1, 1)";

/** Owns the visible lifetime, including a close that interrupts an opening. */
class SurfaceTransition {
  animations = [];
  revision = 0;
  root;
  panel;
  enterDuration;
  exitDuration;

  // Written as plain fields (not parameter properties) so the class can also be
  // loaded by Node's strip-only TypeScript mode in the reader surface tests.
  constructor(root, panel, enterDuration = 300, exitDuration = 200) {
    this.root = root;
    this.panel = panel;
    this.enterDuration = enterDuration;
    this.exitDuration = exitDuration;
  }

  show(reduced) {
    this.run(true, reduced);
  }

  hide(reduced, finished = () => {}) {
    this.run(false, reduced, finished);
  }

  finish() {
    this.animations.forEach((animation) => animation.finish());
  }

  dispose() {
    this.revision++;
    this.animations.forEach((animation) => animation.cancel());
    this.animations = [];
  }

  run(show, reduced, finished) {
    const revision = ++this.revision;
    const hidden = this.root.hidden;
    const opacity = hidden ? "0" : getComputedStyle(this.root).opacity;
    const transform = this.panel
      ? hidden
        ? "translateY(12px)"
        : getComputedStyle(this.panel).transform
      : undefined;
    this.animations.forEach((animation) => animation.cancel());
    this.animations = [];
    this.root.hidden = false;
    this.root.dataset.transition = show ? "opening" : "closing";
    const complete = () => {
      if (revision !== this.revision) return;
      this.root.hidden = !show;
      this.root.dataset.transition = show ? "open" : "closed";
      this.animations.forEach((animation) => animation.cancel());
      this.animations = [];
      finished?.();
    };
    if (reduced || (!show && hidden)) {
      complete();
      return;
    }
    const options = {
      duration: show ? this.enterDuration : this.exitDuration,
      easing: show ? enterEase : exitEase,
      fill: "both",
    };
    const fade = this.root.animate(
      [{ opacity }, { opacity: show ? 1 : 0 }],
      options,
    );
    this.animations.push(fade);
    if (this.panel) {
      this.animations.push(
        this.panel.animate(
          [
            { transform },
            { transform: show ? "translateY(0)" : "translateY(8px)" },
          ],
          options,
        ),
      );
    }
    void fade.finished.then(complete).catch(() => {});
  }
}
