"""把使用说明.md 转成一份自带样式的 使用说明.html（方便双击直接看）。

只支持说明文档里用到的语法：标题、列表、表格、引用、分隔线、粗体、行内代码、图片、链接。
"""

from __future__ import annotations

import html
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CSS = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body {
  margin: 0 auto; padding: 48px 28px 96px; max-width: 880px;
  font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", system-ui, sans-serif;
  font-size: 16px; line-height: 1.85; color: #24292f; background: #ffffff;
}
h1 { font-size: 30px; margin: 0 0 8px; padding-bottom: 14px; border-bottom: 3px solid #4f8cff; }
h2 { font-size: 23px; margin: 44px 0 12px; padding-left: 12px; border-left: 5px solid #4f8cff; }
h3 { font-size: 19px; margin: 30px 0 10px; color: #1f4fa8; }
h4 { font-size: 17px; margin: 22px 0 8px; }
p { margin: 12px 0; }
ul, ol { margin: 12px 0; padding-left: 26px; }
li { margin: 6px 0; }
a { color: #2f6fe4; }
code {
  background: #f0f2f7; padding: 2px 6px; border-radius: 5px;
  font-family: Consolas, "Courier New", monospace; font-size: 14px;
}
blockquote {
  margin: 18px 0; padding: 12px 18px; background: #f3f7ff;
  border-left: 5px solid #4f8cff; border-radius: 0 8px 8px 0; color: #33405c;
}
blockquote p { margin: 6px 0; }
hr { border: 0; border-top: 1px solid #e3e7ef; margin: 40px 0; }
table { border-collapse: collapse; margin: 18px 0; width: 100%; }
th, td { border: 1px solid #dfe3ec; padding: 10px 14px; text-align: left; }
th { background: #f5f7fb; font-weight: 600; }
tr:nth-child(even) td { background: #fafbfe; }
img { max-width: 100%; border: 1px solid #e3e7ef; border-radius: 10px; margin: 14px 0;
      box-shadow: 0 6px 20px rgba(20, 40, 90, 0.08); }
.kbd { display: inline-block; padding: 1px 8px; border-radius: 6px; background: #eef2fa;
       border: 1px solid #ccd6ea; font-weight: 600; }
@media (prefers-color-scheme: dark) {
  body { color: #e6e9f0; background: #16181e; }
  h3 { color: #8fb6ff; }
  code { background: #262a35; }
  blockquote { background: #1d2130; color: #c3cad9; }
  th { background: #22262f; }
  tr:nth-child(even) td { background: #1b1e26; }
  th, td { border-color: #333846; }
  hr { border-top-color: #2b303c; }
  img { border-color: #2b303c; }
  .kbd { background: #262a35; border-color: #3a4152; }
}
"""

INLINE_CODE = re.compile(r"`([^`]+)`")
BOLD = re.compile(r"\*\*([^*]+)\*\*")
IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def inline(text: str) -> str:
    text = html.escape(text, quote=False)
    text = IMAGE.sub(lambda m: f'<img src="{m.group(2)}" alt="{m.group(1)}">', text)
    text = LINK.sub(lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', text)
    text = BOLD.sub(lambda m: f"<strong>{m.group(1)}</strong>", text)
    text = INLINE_CODE.sub(lambda m: f"<code>{m.group(1)}</code>", text)
    return text


def convert(markdown: str) -> str:
    lines = markdown.splitlines()
    out: list[str] = []
    list_stack: list[str] = []
    in_table = False
    quote: list[str] = []

    def close_lists() -> None:
        while list_stack:
            out.append(f"</{list_stack.pop()}>")

    def flush_quote() -> None:
        if quote:
            body = " ".join(quote).strip()
            out.append(f"<blockquote><p>{inline(body)}</p></blockquote>")
            quote.clear()

    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        if stripped.startswith("|") and stripped.endswith("|"):
            flush_quote()
            close_lists()
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if not in_table:
                out.append("<table><thead><tr>" +
                           "".join(f"<th>{inline(c)}</th>" for c in cells) +
                           "</tr></thead><tbody>")
                in_table = True
            elif all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
                pass  # 分隔行
            else:
                out.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in cells) + "</tr>")
            index += 1
            continue
        if in_table:
            out.append("</tbody></table>")
            in_table = False

        if not stripped:
            flush_quote()
            close_lists()
            index += 1
            continue

        if stripped.startswith(">"):
            quote.append(stripped.lstrip("> ").strip())
            index += 1
            continue
        flush_quote()

        if re.fullmatch(r"-{3,}|\*{3,}", stripped):
            close_lists()
            out.append("<hr>")
            index += 1
            continue

        heading = re.match(r"^(#{1,4})\s+(.*)$", stripped)
        if heading:
            close_lists()
            level = len(heading.group(1))
            out.append(f"<h{level}>{inline(heading.group(2))}</h{level}>")
            index += 1
            continue

        bullet = re.match(r"^[-*]\s+(.*)$", stripped)
        if bullet:
            if not list_stack or list_stack[-1] != "ul":
                close_lists()
                out.append("<ul>")
                list_stack.append("ul")
            out.append(f"<li>{inline(bullet.group(1))}</li>")
            index += 1
            continue

        numbered = re.match(r"^\d+[.)]\s+(.*)$", stripped)
        if numbered:
            if not list_stack or list_stack[-1] != "ol":
                close_lists()
                out.append("<ol>")
                list_stack.append("ol")
            out.append(f"<li>{inline(numbered.group(1))}</li>")
            index += 1
            continue

        close_lists()
        out.append(f"<p>{inline(stripped)}</p>")
        index += 1

    flush_quote()
    if in_table:
        out.append("</tbody></table>")
    close_lists()
    return "\n".join(out)


def main() -> int:
    source = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "使用说明.md")
    target = sys.argv[2] if len(sys.argv) > 2 else os.path.join(ROOT, "使用说明.html")
    with open(source, "r", encoding="utf-8") as fh:
        markdown = fh.read()
    title = "连点器 AutoClicker Pro 使用说明"
    for line in markdown.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            break
    document = (
        "<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"<title>{html.escape(title)}</title>\n<style>{CSS}</style>\n</head>\n<body>\n"
        f"{convert(markdown)}\n</body>\n</html>\n"
    )
    with open(target, "w", encoding="utf-8") as fh:
        fh.write(document)
    print("已生成", target, len(document), "字符")
    return 0


if __name__ == "__main__":
    sys.exit(main())
