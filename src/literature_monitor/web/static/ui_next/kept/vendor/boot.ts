// Adapted from Blog Theme 6f1e5b0, Copyright (c) 2026 LBEILC, MIT.
// Identity, SVG scan and original Welcome only; no authentication or 3D scene.
import { bootMotion } from "./boot-motion";
import { bootMarkContour } from "./brand";
import { BootLettering } from "./boot-lettering";

const ns = "http://www.w3.org/2000/svg";
const arc = (r: number, start: number, sweep: number, x = 960, y = 540) => {
  const point = (a: number) => `${x + Math.cos(a) * r},${y + Math.sin(a) * r}`;
  if (sweep >= Math.PI * 1.999)
    return `M${point(start)}A${r},${r} 0 1 1 ${point(start + Math.PI)}A${r},${r} 0 1 1 ${point(start + Math.PI * 2)}`;
  return `M${point(start)}A${r},${r} 0 ${sweep > Math.PI ? 1 : 0} 1 ${point(start + sweep)}`;
};

export class BootSequence {
  private nodes: Map<string, HTMLElement> = new Map();
  private contour: SVGPathElement;
  private letters: SVGTextElement;
  private plus: SVGPathElement;
  private minus: SVGPathElement;
  private brandLines: HTMLElement[];
  private scanPaths: SVGPathElement[];
  private scanGroup: SVGElement;
  private orbitDots: SVGCircleElement[];
  private core: SVGCircleElement;
  private satellites: SVGCircleElement[];
  private caps: SVGCircleElement[];
  private accessLettering: BootLettering;
  private authLettering: BootLettering;
  private brandLettering: BootLettering;
  private companyInk: HTMLElement[];
  constructor(private stage: HTMLElement) {
    [
      ".access-text",
      ".boot-logo",
      ".auth-status",
      "#auth-message",
      ".scan",
      ".scan > span",
      ".welcome",
      ".welcome-heading",
      ".welcome-panel",
      ".welcome-company",
      ".welcome-highlight",
      ".welcome-database",
      ".welcome-logo",
      ".boot-white",
      ".brand",
      ".powered",
      "#boot-background",
      ".boot-background svg",
    ].forEach((s) => this.nodes.set(s, stage.querySelector<HTMLElement>(s)!));
    const mark = stage.querySelector<SVGSVGElement>(".boot-logo svg")!;
    const original = mark.querySelector("path")!;
    this.contour = original;
    this.contour.setAttribute("d", bootMarkContour);
    this.contour.setAttribute("pathLength", "1");
    const symbols = mark.querySelector("path:not([pathLength])")!;
    this.plus = document.createElementNS(ns, "path");
    this.plus.setAttribute("d", "M44 70h50M69 45v50");
    this.minus = document.createElementNS(ns, "path");
    this.minus.setAttribute("d", "M219 70h44");
    [this.plus, this.minus].forEach((p) => {
      p.setAttribute("stroke", "currentColor");
      p.setAttribute("stroke-width", "15");
      mark.insertBefore(p, symbols);
    });
    symbols.remove();
    this.letters = mark.querySelector("text")!;
    this.letters.setAttribute("text-anchor", "start");
    this.letters.setAttribute("x", "20");
    this.brandLines = Array.from(
      stage.querySelector(".brand")!.children,
    ) as HTMLElement[];
    this.scanPaths = Array.from(
      stage.querySelectorAll<SVGPathElement>(".scan path"),
    );
    this.scanGroup = this.scanPaths[0].parentElement as unknown as SVGElement;
    this.orbitDots = Array.from(
      stage.querySelectorAll<SVGCircleElement>(".scan .orbit-dot"),
    );
    this.core = stage.querySelector(".scan .scan-core")!;
    this.core.setAttribute("cx", "959.5");
    this.core.setAttribute("cy", "539.5");
    this.satellites = Array.from({ length: 6 }, () => {
      const dot = document.createElementNS(ns, "circle");
      dot.classList.add("satellite-dot");
      dot.setAttribute("fill", "#080a08");
      dot.setAttribute("stroke", "none");
      // Draw beneath the core so its large flashes naturally cover the orbit.
      this.core.parentElement!.insertBefore(dot, this.core);
      return dot;
    });
    this.caps = ["#080a08", "#fff"].map((fill) => {
      const cap = document.createElementNS(ns, "circle");
      cap.setAttribute("fill", fill);
      cap.setAttribute("stroke", "none");
      this.core.parentElement!.appendChild(cap);
      return cap;
    });
    // 绑定 lettering：必须在收集完扫描圆环路径之后——图形自身含 SVG path，
    // 早于 this.scanPaths 收集会被当成圆环几何。
    this.accessLettering = new BootLettering(this.el(".access-text"), ["access"]);
    this.authLettering = new BootLettering(this.el("#auth-message"), [
      "request",
      "processing",
      "processingGlitch",
    ]);
    this.brandLettering = new BootLettering(this.brandLines[0], ["brand"]);
    this.brandLettering.setText("RHINE LAB");
    this.companyInk = Array.from(stage.querySelectorAll<HTMLElement>(".welcome-company strong"));
    for (const [selector, key, text] of [
      [".welcome-heading", "welcome", "WELCOME TO"],
      [".welcome-database", "database", "INTERNAL DATABASE"],
    ] as const) new BootLettering(this.el(selector), [key]).setText(text);
    this.companyInk.forEach(el => {
      const ink = document.createElement("span");
      el.replaceChildren(ink);
      new BootLettering(ink, ["company"]).setText("RHINE LAB.LLC.");
    });
  }

  private el(selector: string) {
    return this.nodes.get(selector)!;
  }
  private opacity(selector: string, value: number | boolean) {
    this.el(selector).style.opacity = String(Number(value));
  }
  update(time: number, identityLabel: string) {
    const s = bootMotion(time, identityLabel),
      t = s.t;
    this.stage.dataset.bootFrame = String(s.f);
    this.accessLettering.setText(s.access);
    this.opacity(".access-text", s.accessOpacity);
    this.opacity(".boot-logo", s.logoOpacity);
    this.el(".boot-logo").style.transform =
      `translate(${s.logo.offsetX}px, 1px)`;
    this.contour.style.strokeDasharray = `${s.logo.length} ${1 - s.logo.length}`;
    this.contour.style.strokeDashoffset = String(-s.logo.start);
    this.contour.setAttribute("stroke-width", String(s.logo.strokeWidth));
    // Preserve the SVG text node once each revealed letter is in place. Replacing
    // it every frame invalidates glyph rasterization under the moving HUD.
    if (this.letters.textContent !== s.logoLetters)
      this.letters.textContent = s.logoLetters;
    this.plus.style.opacity = this.minus.style.opacity =
      s.logo.symbolScale > 0 ? "1" : "0";
    this.plus.setAttribute(
      "transform",
      `translate(${s.logo.plusX} 70) rotate(${s.logo.plusAngle}) scale(${s.logo.symbolScale}) translate(-69 -70)`,
    );
    this.minus.setAttribute(
      "d",
      `M${-s.logo.minusWidth / 2} 0h${s.logo.minusWidth}`,
    );
    this.minus.setAttribute(
      "transform",
      `translate(${s.logo.minusX} 70) scale(${s.logo.symbolScale})`,
    );
    this.opacity(".auth-status", s.authOpacity);
    // A saved local profile is a display input, not an authenticated identity.
    this.authLettering.setText(s.auth.replace("ID CONFIRMED", "LOCAL PROFILE"));
    this.opacity(".brand", 1);
    this.el(".brand").style.transform = "none";
    this.brandLines.forEach((node, i) => {
      node.style.opacity = String(s.brand[i].opacity);
      node.style.transform = `translateX(${s.brand[i].x}px)`;
    });
    this.opacity(".powered", s.poweredLetters > 0);
    this.el(".powered").style.clipPath =
      `inset(0 ${100 * (1 - s.poweredLetters / 19)}% 0 0)`;
    this.opacity(".scan", s.scanVisible);
    if (s.scanVisible) this.renderScan(s);
    this.opacity(".welcome", s.welcomeVisible ? s.welcomeOpacity : 0);
    this.el(".welcome").style.transform = `scale(${s.welcomeScale})`;
    this.el(".welcome").style.filter =
      `blur(${s.exitBlur}px) invert(${s.exit * 0.22}) sepia(${s.exit}) saturate(${1 + s.exit * 5}) hue-rotate(${s.exit * 115}deg)`;
    this.opacity(".welcome-panel", s.welcomePanel);
    this.opacity(".welcome-heading", 1);
    this.el(".welcome-heading").style.color = `rgb(${255 * (1 - s.welcomeInk)} ${255 * (1 - s.welcomeInk)} ${255 * (1 - s.welcomeInk)})`;
    this.opacity(".welcome-company", s.companyVisible ? (s.companyMask ? 0.65 : 1) : 0);
    this.companyInk[1].querySelector<HTMLElement>("span")!.style.opacity = s.companyMask ? ".06" : "1";
    this.el(".welcome-highlight").style.clipPath = `inset(0 ${100 * (1 - s.highlight)}% 0 0)`;
    this.opacity(".welcome-database", s.databaseOpacity);
    this.opacity(".welcome-logo", s.welcomeLogo);
    this.opacity(".boot-white", s.white);
    this.opacity("#boot-background", s.backgroundOpacity);
    this.el(".boot-background svg").style.transform =
      `translate(${Math.sin(t * 0.16) * 18}px, ${-(t - 6) * 5}px) scale(1.08)`;
    return s;
  }
  setAuthText(text: string) { this.authLettering.setText(text); }
  setLogoCut(start: number) { this.contour.style.strokeDashoffset = String(-start); }
  renderScan(s: ReturnType<typeof bootMotion>) {
    const { scan } = s,
      r = scan.radius;
    const group = this.scanGroup;
    group.setAttribute(
      "transform",
      `translate(960 540) scale(${s.ringScale}) translate(-960 -540)`,
    );
    group.style.opacity = String(s.ringOpacity);
    group.style.filter = `blur(${s.ringBlur}px)`;
    this.scanPaths[0].setAttribute(
      "d",
      arc(r, scan.outerStart, scan.outerSweep),
    );
    this.scanPaths[0].setAttribute("stroke-width", "2.4");
    this.scanPaths[1].setAttribute(
      "d",
      arc(scan.whiteRadius, scan.whiteStart, scan.whiteSweep),
    );
    this.scanPaths[1].setAttribute("stroke-width", "4");
    const inner = scan.innerStart;
    this.scanPaths[2].setAttribute(
      "d",
      arc(scan.innerRadius, inner, scan.innerSweep),
    );
    this.scanPaths[3].setAttribute(
      "d",
      arc(scan.innerRadius, inner + Math.PI, scan.innerSweep),
    );
    s.scanOrbit.sides.forEach((side, i) => {
      this.scanPaths[i + 4].setAttribute(
        "d",
        arc(side.radius, side.start, side.sweep, side.x, side.y),
      );
      this.scanPaths[i + 4].style.opacity = s.scanOrbit.sideVisible ? "1" : "0";
    });
    s.scanOrbit.satellites.forEach((point, i) => {
      const dot = this.satellites[i];
      dot.setAttribute("cx", String(point.x));
      dot.setAttribute("cy", String(point.y));
      dot.setAttribute("r", String(point.radius));
    });
    this.core.style.opacity = s.ornament ? "1" : "0";
    this.core.setAttribute("r", String(s.coreRadius));
    const orbit = scan.orbit;
    this.orbitDots.forEach((dot, i) => {
      dot.setAttribute(
        "cx",
        String(960 + Math.cos(orbit + i * Math.PI) * scan.orbitRadius),
      );
      dot.setAttribute(
        "cy",
        String(540 + Math.sin(orbit + i * Math.PI) * scan.orbitRadius),
      );
      dot.setAttribute("r", String(scan.dotRadius));
    });
    this.caps.forEach((cap, i) => {
      const angle = i ? scan.whiteStart : scan.outerStart + scan.outerSweep;
      const radius = i ? scan.whiteRadius : r;
      cap.setAttribute("cx", String(960 + Math.cos(angle) * radius));
      cap.setAttribute("cy", String(540 + Math.sin(angle) * radius));
      cap.setAttribute("r", String(i ? scan.whiteCap : scan.blackCap));
    });
    this.opacity(".scan > span", s.permissionOpacity);
    this.el(".scan > span").style.letterSpacing = `${s.scanTracking}px`;
    // 描边图形的收束靠单元格间距，与文字的字距同步写入。
    this.el(".scan > span").style.setProperty("--boot-phrase-tracking", `${s.scanTracking}px`);
    this.el(".scan > span").style.fontSize = `${s.scanFont}px`;
  }
}
