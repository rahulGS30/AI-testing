from typing import Any

from playwright.sync_api import Page


def observe_page(page: Page) -> dict[str, Any]:
    elements = page.locator(
        "button, a[href], input, textarea, select, [role='button'], [role='link']"
    ).evaluate_all(
        """nodes => nodes.slice(0, 1000).map(node => {
          const style = window.getComputedStyle(node);
          const rect = node.getBoundingClientRect();
          const visible = style.display !== 'none' && style.visibility !== 'hidden'
            && rect.width > 0 && rect.height > 0;
          const label = node.getAttribute('aria-label')
          || node.getAttribute('title')
          || node.querySelector('svg[aria-label]')?.getAttribute('aria-label')
          || node.querySelector('svg title')?.textContent
          || node.getAttribute('placeholder')
          || node.innerText
          || node.getAttribute('value')
          || '';
          const href = node.getAttribute('href') || '';
          const searchControl = /(^|\\/)search(?:\\/|$|\\?)/i.test(href);
          const placeholder = node.getAttribute('placeholder') || '';
          const priority = /search/i.test(`${label} ${placeholder} ${href}`)
          ? 0
          : node.matches('input:not([type="hidden"]), textarea, select') ? 1 : 2;
          return {
          tag: node.tagName.toLowerCase(),
          role: node.getAttribute('role') || '',
          type: node.getAttribute('type') || '',
          label: (searchControl && !label.trim() ? 'Search' : label)
            .trim().replace(/\\s+/g, ' ').slice(0, 160),
          placeholder,
          href: href.slice(0, 180),
          visible,
          priority
          };
        }).filter(item => item.visible).sort((a, b) => a.priority - b.priority)"""
    )
    visible_text = page.locator("body").inner_text(timeout=5000)
    return {
        "url": page.url,
        "title": page.title(),
        "visible_text": visible_text[:12000],
        "interactive_elements": [
            {key: value for key, value in item.items() if key != "priority"}
            for item in elements[:100]
        ],
    }
