"""Small page helpers shared by the browser tests."""


def open_mode(page, mode: str) -> None:
    page.click(f'.mode-btn[data-mode="{mode}"]')
    page.wait_for_timeout(200)


def open_tab(page, mode: str, tab: str) -> None:
    page.click(f'#mode-{mode} .tab-btn[data-tab="{tab}"]')
    page.wait_for_timeout(500)
