        (() => {
            const mode = __UTIKI_LOGIN_GUARD_ARG_0__;
            const expectedUrl = __UTIKI_LOGIN_GUARD_ARG_1__;
            const expectedDocument = __UTIKI_LOGIN_GUARD_ARG_2__;
            const expectedOrigin = __UTIKI_LOGIN_GUARD_ARG_3__;
            const accountValue = __UTIKI_LOGIN_GUARD_ARG_4__;
            const passwordValue = __UTIKI_LOGIN_GUARD_ARG_5__;
            const writeAccount = __UTIKI_LOGIN_GUARD_ARG_6__;
            const writePassword = __UTIKI_LOGIN_GUARD_ARG_7__;
            const result = status => JSON.stringify({
                page_url: location.href,
                document_id: String(performance.timeOrigin),
                status: status
            });
            const samePage = () => {
                const url = new URL(location.href);
                return location.href === expectedUrl &&
                    String(performance.timeOrigin) === expectedDocument &&
                    url.origin === expectedOrigin && !url.username && !url.password;
            };
            const visible = element => {
                if (!element || !element.isConnected || element.hidden ||
                    element.closest('[hidden], [inert], [aria-hidden="true"]')) return false;
                const style = getComputedStyle(element);
                return style.display !== 'none' && style.visibility !== 'hidden' &&
                    element.getClientRects().length > 0;
            };
            const usable = element => visible(element) && !element.disabled &&
                element.getAttribute('aria-disabled') !== 'true' && !element.readOnly;
            if (!samePage() || (mode !== 'write' && mode !== 'audit')) return result('rejected');
            const accounts = Array.from(document.querySelectorAll('#LOGIN_ID'));
            const passwords = Array.from(document.querySelectorAll('#LOGIN_PWD'));
            if (accounts.length !== 1 || passwords.length !== 1 ||
                !usable(accounts[0]) || !usable(passwords[0]) ||
                Array.from(document.querySelectorAll('#dialog-message')).some(visible)) {
                return result('rejected');
            }
            const account = accounts[0];
            const password = passwords[0];
            const matched = () => samePage() && usable(account) && usable(password) &&
                account.value === accountValue && password.value === passwordValue;
            if (mode === 'audit') return result(matched() ? 'applied' : 'rejected');
            if ((writeAccount && account.value.trim()) ||
                (!writeAccount && account.value !== accountValue) ||
                (writePassword && password.value) ||
                (!writePassword && password.value !== passwordValue)) return result('rejected');
            __UTIKI_NATIVE_INPUT_SETTER_FRAGMENT__
            let mutated = false;
            try {
                if (writeAccount) {
                    mutated = true;
                    if (!setNativeInputValue(account, accountValue)) return result('unknown');
                }
                if (!samePage() || !usable(account) || !usable(password)) return result('unknown');
                if (writePassword) {
                    mutated = true;
                    if (!setNativeInputValue(password, passwordValue)) return result('unknown');
                }
                return result(matched() ? 'applied' : 'unknown');
            } catch (_) {
                return result(mutated ? 'unknown' : 'rejected');
            }
        })()
