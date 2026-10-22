        (() => {
            const intent = __UTIKI_REJECTED_LOGIN_ARG_0__;
            const identity = () => location.href === intent.pageUrl &&
                String(performance.timeOrigin) === intent.documentId &&
                location.origin === intent.origin;
            const visible = element => {
                if (!element || !element.isConnected || element.hidden ||
                    element.closest('[hidden], [inert], [aria-hidden="true"]')) return false;
                const style = getComputedStyle(element);
                return style.display !== 'none' && style.visibility !== 'hidden' &&
                    element.getClientRects().length > 0;
            };
            const fields = () => {
                const accounts = Array.from(document.querySelectorAll('#LOGIN_ID'));
                const passwords = Array.from(document.querySelectorAll('#LOGIN_PWD'));
                if (!identity() || accounts.length !== 1 || passwords.length !== 1 ||
                    !visible(accounts[0]) || !visible(passwords[0]) ||
                    accounts[0].disabled || passwords[0].disabled ||
                    accounts[0].readOnly || passwords[0].readOnly ||
                    Array.from(document.querySelectorAll('#dialog-message')).some(visible)) {
                    return null;
                }
                return [accounts[0], passwords[0]];
            };
            const key = Symbol.for('utiki.rejected-login-observation');
            const pair = fields();
            const result = changedReady => JSON.stringify({
                page_url: location.href,
                document_id: String(performance.timeOrigin),
                token: intent.token,
                changed_ready: changedReady
            });
            if (!pair) return result(false);
            if (intent.mode === 'arm') {
                const old = document.documentElement[key];
                if (old && old.token !== intent.token) return result(false);
                if (!old) {
                    const marker = {
                        token: intent.token, url: intent.pageUrl,
                        document: intent.documentId, pair,
                        before: pair.map(input => input.value),
                        trusted: [null, null]
                    };
                    document.documentElement[key] = marker;
                    pair.forEach((input, index) => input.addEventListener('input', event => {
                        if (event.isTrusted && document.documentElement[key] === marker &&
                            identity() && event.target === input) {
                            marker.trusted[index] = input.value;
                        }
                    }));
                }
                return result(true);
            }
            if (intent.mode !== 'observe') return result(false);
            const marker = document.documentElement[key];
            return result(!!marker && marker.token === intent.token &&
                marker.pair[0] === pair[0] && marker.pair[1] === pair[1] &&
                pair[0].value.trim().length > 0 && pair[1].value.length > 0 &&
                pair.every((input, index) => input.value === marker.before[index] ||
                    input.value === marker.trusted[index]) &&
                marker.trusted.some((value, index) =>
                    value !== null && value !== marker.before[index] &&
                    pair[index].value === value));
        })()
