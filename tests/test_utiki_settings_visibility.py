"""Exercise the actual settings-page visibility functions without a live site."""

import shutil
import subprocess
import unittest
from pathlib import Path


SETTINGS_JS = Path(__file__).resolve().parents[1] / "src" / "www" / "settings.js"
SETTINGS_HTML = SETTINGS_JS.with_name("settings.html")


class UtikiSettingsVisibilityTest(unittest.TestCase):
    def test_shared_fields_and_tenant_credentials_switch_independently(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js is unavailable")

        script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const js = fs.readFileSync(process.argv[1], 'utf8');
const html = fs.readFileSync(process.argv[2], 'utf8');
const start = js.indexOf('// Check if URL is Tixcraft family');
const end = js.indexOf('// Soft-block delay', start);
if (start < 0 || end < 0) throw new Error('Visibility functions not found');

function rowTokens(id) {
    const escaped = id.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const pattern = new RegExp('data-under="([^"]+)"\\s*>\\s*<label[^>]*for="' + escaped + '"');
    const match = html.match(pattern);
    if (!match) throw new Error('Missing settings row: ' + id);
    return match[1].split(/\s+/);
}

const rows = {
    seat: {tokens: rowTokens('utiki_seat_selection_mode'), visible: false},
    ticket: {tokens: rowTokens('utiki_ticket_type_keyword'), visible: false},
    sorting: {tokens: rowTokens('utiki_seat_sorting'), visible: false},
    cookie: {tokens: rowTokens('ctbc_cookie'), visible: false},
    dragonsCookie: {tokens: rowTokens('wdragons_cookie'), visible: false},
    account: {tokens: rowTokens('ctbc_account'), visible: false},
};
const allRows = Object.values(rows);
function $(selector) {
    if (selector !== '[data-under]') throw new Error('Unexpected selector: ' + selector);
    return {
        addClass() {
            for (const row of allRows) row.visible = false;
            return {
                filter(filterSelector) {
                    const match = filterSelector.match(/^\[data-under~="([^"]+)"\]$/);
                    if (!match) throw new Error('Unexpected filter: ' + filterSelector);
                    return {
                        removeClass() {
                            for (const row of allRows) {
                                if (row.tokens.includes(match[1])) row.visible = true;
                            }
                        }
                    };
                }
            };
        }
    };
}
const context = {
    URL, $,
    updateTixcraftSoftBlockDelayVisibility() {},
    updateCitylineHintVisibility() {},
};
const api = vm.runInNewContext(js.slice(start, end) + '\n({detectPlatform, updatePlatformFields, isTixcraftSoftBlockScope})', context);
function verify(url, shared, ctbcCredentials, dragonsCookie) {
    api.updatePlatformFields(url);
    for (const key of ['seat', 'ticket', 'sorting']) {
        if (rows[key].visible !== shared) throw new Error(key + ' visibility for ' + url);
    }
    for (const key of ['cookie', 'account']) {
        if (rows[key].visible !== ctbcCredentials) throw new Error(key + ' visibility for ' + url);
    }
    if (rows.dragonsCookie.visible !== dragonsCookie) throw new Error('Dragons cookie visibility for ' + url);
}
if (api.detectPlatform('https://tix.wdragons.com/') !== 'utiki') throw new Error('Dragons is not uTiki');
verify('https://tix.ctbcsports.com/BROTHERS/', true, true, false);
verify('https://tix.wdragons.com/', true, false, true);
verify('https://tixcraft.com/', false, false, false);
verify('https://tix.ctbcsports.com/BROTHERS/', true, true, false);
verify('https://tix.wdragons.com/?next=tix.ctbcsports.com', true, false, true);
verify('https://tix.wdragons.com/UTK0201_?next=https://tixcraft.com/', true, false, true);
verify('http://tix.wdragons.com/', false, false, false);
verify('https://tix.wdragons.com.evil.test/', false, false, false);
verify('https://tix.wdragons.com.evil.test/?next=tixcraft.com', false, false, false);
if (api.isTixcraftSoftBlockScope('https://tix.wdragons.com/?next=tixcraft.com')) throw new Error('uTiki query showed soft block');
if (api.isTixcraftSoftBlockScope('https://tix.wdragons.com.evil.test/?next=tixcraft.com')) throw new Error('Fake host showed soft block');
if (!api.isTixcraftSoftBlockScope('https://sub.tixcraft.com/')) throw new Error('TixCraft subdomain lost soft block');
if (api.isTixcraftSoftBlockScope('ftp://tixcraft.com/')) throw new Error('Non-web scheme showed soft block');
if (api.detectPlatform('ftp://tixcraft.com/') !== null) throw new Error('Non-web scheme classified');
if (api.detectPlatform('http://tixcraft.com/') !== 'tixcraft') throw new Error('HTTP TixCraft lost');
if (api.detectPlatform('https://ticketmaster.sg.evil.test/') !== null) throw new Error('Fake Ticketmaster host classified');
if (api.detectPlatform('https://www.ticketmaster.sg/') !== 'tixcraft') throw new Error('Ticketmaster SG lost');
for (const host of ['constructor', '__proto__']) {
    if (api.detectPlatform(`https://${host}/`) !== null) throw new Error('Prototype hostname classified as uTiki: ' + host);
}
for (const [url, expected] of [
    ['https://sub.tixcraft.com/', 'tixcraft'],
    ['https://kktix.com/', 'kktix'],
    ['https://ibon.com/', 'ibon'],
    ['https://famiticket.com/', 'famiticket'],
    ['https://kham.com.tw/', 'kham'],
    ['https://ticket.com.tw/', 'ticket'],
    ['https://udnfunlife.com/', 'udn'],
    ['https://ticketplus.com/', 'ticketplus'],
    ['https://cityline.com/', 'cityline'],
    ['https://hkticketing.com/', 'hkticketing'],
    ['https://funone.io/', 'funone'],
    ['https://fansi.me/', 'fansigo'],
    ['https://urbtix.hk/', 'urbtix'],
]) {
    if (api.detectPlatform(url) !== expected) throw new Error('Platform host lost: ' + url);
    if (api.detectPlatform(`https://example.test/?next=${encodeURIComponent(url)}`) !== null) {
        throw new Error('Query host collision: ' + url);
    }
}
"""
        result = subprocess.run(
            [node, "-e", script, str(SETTINGS_JS), str(SETTINGS_HTML)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
