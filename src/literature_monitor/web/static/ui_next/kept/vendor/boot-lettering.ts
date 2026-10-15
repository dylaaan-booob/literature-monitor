import artwork from "./boot-lettering-art.json";
import "./boot-lettering.css";
type PhraseKey = keyof typeof artwork;
const ns = "http://www.w3.org/2000/svg";

/** Fixed phrase reveal cells, backed by licensed webfonts or authored artwork. */
export class BootLettering {
  private label = document.createElement("span");
  private phrases: {
    text: string;
    node: HTMLSpanElement;
    letters: HTMLSpanElement[];
    weight: string;
  }[];
  private value: string | undefined;
  private exact: string | undefined;

  constructor(private host: HTMLElement, keys: PhraseKey[]) {
    this.label.className = "boot-phrase-label";
    this.phrases = keys.map((key) => {
      const art = artwork[key];
      const node = document.createElement("span");
      node.className = "boot-phrase";
      node.dataset.phrase = key;
      node.dataset.weight = art.weight;
      node.setAttribute("aria-hidden", "true");
      node.hidden = true;
      const letters = art.letters.map((letter) => {
        const cell = document.createElement("span");
        cell.className = "boot-phrase-letter";
        cell.style.width = `${letter.width}em`;
        if (letter.path) {
          const svg = document.createElementNS(ns, "svg");
          svg.classList.add("boot-letter-art");
          svg.setAttribute("viewBox", `0 0 ${letter.width * art.units} ${art.units}`);
          svg.setAttribute("focusable", "false");
          const path = document.createElementNS(ns, "path");
          path.setAttribute("d", letter.path);
          svg.append(path);
          cell.append(svg);
        }
        node.append(cell);
        return cell;
      });
      return { text: art.text, node, letters, weight: art.weight };
    });
    host.classList.add("has-boot-lettering");
    host.replaceChildren(this.label, ...this.phrases.map((p) => p.node));
    host.dataset.letteringRenderer = "artwork";
  }

  /**
   * `exactText`（本站新增）用于身份行：身份行会按动态注册名逐字显示，多个短语共享
   * "ID CONFIRMED : " 前缀时无法只靠前缀判断目标，调用方把完整目标文案传进来即可。
   * 传入值与目标不构成前缀关系时（例如同一宿主后续显示 REQUEST RECEIVED），
   * 自动退回上游的"前缀匹配 + 未命中回退文本"行为。
   */
  setText(value: string, exactText?: string) {
    if (this.value === value && this.exact === exactText) return;
    this.value = value;
    this.exact = exactText;
    this.label.textContent = value;
    const exact = exactText && value && exactText.startsWith(value)
      ? this.phrases.find((p) => p.text === exactText)
      : undefined;
    const active = value ? exact ?? this.phrases.find((p) => p.text.startsWith(value)) : undefined;
    // A new, unauthored phrase remains readable until its artwork is exported.
    this.host.classList.toggle("boot-lettering-fallback", Boolean(value && !active));
    for (const phrase of this.phrases) {
      const visible = phrase === active;
      if (phrase.node.hidden === visible) phrase.node.hidden = !visible;
      if (!visible) continue;
      phrase.letters.forEach((letter, i) => {
        const hidden = i >= value.length;
        if (letter.hidden !== hidden) letter.hidden = hidden;
      });
    }
  }
}
