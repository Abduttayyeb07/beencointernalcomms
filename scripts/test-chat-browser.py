"""Exercise the chat UI using isolated API fixtures; no real workspace data is written."""
import json
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts" / "chat"
OUT.mkdir(parents=True, exist_ok=True)
users = [dict(id="u1", displayName="Alex Morgan", username="alex", email="alex@example.test", role="admin", presence="online", isOnline=True, isActive=True, statusText="Working together", groups=[]),
         dict(id="u2", displayName="Sam Rivera", username="sam", email="sam@example.test", role="member", presence="online", isOnline=True, isActive=True, groups=[])]
channels = [dict(id="general", slug="general", name="general", type="public", topic="The place for team conversations", members=["u1", "u2"], ownerIds=["u1"], unread=0, mentions=0, typingUserIds=[]),
            dict(id="design", slug="design", name="design", type="private", topic="Ideas, feedback, and the details", members=["u1", "u2"], ownerIds=["u1"], unread=2, mentions=0, typingUserIds=[]),
            dict(id="dm", slug="dm", name="Sam Rivera", type="dm", members=["u1", "u2"], ownerIds=[], unread=0, mentions=0, typingUserIds=[])]
messages = [dict(id="m1", channelId="general", authorId="u2", body="Morning team! Here's the plan for our next release.\n\n**Today's focus**\nKeep conversations clear and make the small details feel right.", createdAt="2026-09-14T09:00:00Z", parentId=None, attachments=[], reactions={"👍": ["u1"]}),
            dict(id="m2", channelId="general", authorId="u1", body="The new navigation is ready for review. Channels, direct messages, and files are all in one place.", createdAt="2026-09-14T09:05:00Z", parentId=None, attachments=[], reactions={}),
            dict(id="r1", channelId="general", authorId="u1", body="Looks good. Let's review the mobile layout too.", createdAt="2026-09-14T09:06:00Z", parentId="m1", attachments=[], reactions={}),
            dict(id="m3", channelId="dm", authorId="u2", body="Can you take a look at the latest design?", createdAt="2026-09-14T09:07:00Z", parentId=None, attachments=[], reactions={})]
config = dict(auth={}, files={}, network={})
payload = dict(currentUser=users[0], users=users, channels=channels, messages=messages, config=config)
def api(route):
    path = route.request.url.split('/api/')[1]
    result = {}
    if path == 'session': result = dict(user=users[0], config=config)
    elif path == 'bootstrap': result = payload
    elif path == 'messages' and route.request.method == 'POST':
        body = route.request.post_data_json
        messages.append(dict(id='sent'+str(len(messages)), authorId='u1', createdAt='2026-09-14T10:00:00Z', reactions={}, **body))
        result = dict(ok=True)
    route.fulfill(status=200, content_type='application/json', body=json.dumps(result))

class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args): pass

server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=str(ROOT / 'apps/web')))
threading.Thread(target=server.serve_forever, daemon=True).start()
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport=dict(width=1600, height=900), service_workers='block', reduced_motion='reduce')
        context.add_init_script("localStorage.setItem('beenco-connect-token','fixture-token')")
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route('**/api/**', api)
        page.goto(f'http://127.0.0.1:{server.server_port}')
        expect(page.locator('.channel-title h2')).to_contain_text('general')
        page.screenshot(path=str(OUT / 'desktop.png'))
        assert page.locator('.main').evaluate("el => getComputedStyle(el).backgroundColor") == 'rgb(26, 29, 33)'
        assert not page.locator('.details-panel').is_visible()
        assert page.locator('.composer').bounding_box()['y'] + page.locator('.composer').bounding_box()['height'] <= 900
        page.locator('.reply-link').click()
        expect(page.locator('#threadReplyForm')).to_be_visible()
        page.locator('[data-action="close-details"]').click()
        page.keyboard.press('Control+k')
        page.locator('[data-action="global-search"]').fill('release')
        expect(page.locator('.slack-search-result')).to_have_count(1)
        page.keyboard.press('Escape')
        page.locator('[data-action="composer-draft"]').fill('Browser smoke message')
        page.locator('[data-action="send-message"]').click()
        expect(page.locator('.message-body').filter(has_text='Browser smoke message')).to_have_count(1)
        expect(page.locator('[data-action="composer-draft"]')).to_have_value('')
        page.get_by_role('button', name='Bold', exact=True).click()
        expect(page.locator('[data-action="composer-draft"]')).to_have_value('**text**')
        page.locator('[data-action="composer-draft"]').fill('Keyboard message')
        page.locator('[data-action="composer-draft"]').press('Shift+Enter')
        expect(page.locator('[data-action="composer-draft"]')).to_have_value('Keyboard message\n')
        page.locator('[data-action="composer-draft"]').press('Enter')
        expect(page.locator('.message-body').filter(has_text='Keyboard message')).to_have_count(1)
        page.get_by_role('button', name='DMs', exact=True).click()
        page.locator('.dm-preview').click()
        expect(page.locator('.channel-title h2')).to_contain_text('Sam Rivera')
        page.screenshot(path=str(OUT / 'dms.png'))
        page.get_by_role('button', name='Activity', exact=True).click()
        expect(page.locator('.channel-title h2')).to_have_text('Activity')
        page.get_by_role('button', name='Files', exact=True).first.click()
        expect(page.locator('.channel-title h2')).to_have_text('All files')
        page.get_by_role('button', name='Home', exact=True).click()
        page.set_viewport_size(dict(width=1920, height=1080))
        assert page.locator('.composer').bounding_box()['y'] + page.locator('.composer').bounding_box()['height'] <= 1080
        page.set_viewport_size(dict(width=900, height=1000))
        page.locator('[data-action="open-panel"][data-panel="people"]').first.click()
        expect(page.locator('.details-panel')).to_be_visible()
        page.screenshot(path=str(OUT / 'tablet.png'))
        page.keyboard.press('Escape')
        page.set_viewport_size(dict(width=390, height=844))
        expect(page.locator('.mobile-tabs')).to_be_visible()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.wait_for_function("document.querySelector('.sidebar').getBoundingClientRect().right <= 1")
        assert page.locator('.composer').bounding_box()['y'] + page.locator('.composer').bounding_box()['height'] <= 784
        page.screenshot(path=str(OUT / 'mobile.png'))
        page.locator('.mobile-tabs [data-action="toggle-sidebar"]').click()
        expect(page.locator('.sidebar')).to_have_class(__import__('re').compile('is-open'))
        page.locator('.sidebar [data-channel-id="general"]').click()
        expect(page.locator('.channel-title h2')).to_contain_text('general')
        assert not errors, errors
        print('PASS: dark layout, thread, search, send, DMs, activity, files, mobile navigation; no browser errors')
        browser.close()
finally:
    server.shutdown()
