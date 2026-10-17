
        (() => {
            const expectedDialog = __UTIKI_LOGIN_FINGERPRINT_ARG_0__;
            if (expectedDialog) {
                if (location.href !== expectedDialog.pageUrl ||
                    String(performance.timeOrigin) !==
                        expectedDialog.documentId) {
                    return JSON.stringify({ok: false});
                }
                const visible = element => {
                    if (!element || !element.isConnected || element.hidden ||
                        element.getAttribute('aria-hidden') === 'true') {
                        return false;
                    }
                    const style = getComputedStyle(element);
                    return style.display !== 'none' &&
                        style.visibility !== 'hidden' &&
                        element.getClientRects().length > 0;
                };
                const dialogs = Array.from(
                    document.querySelectorAll('#dialog-message'))
                    .map(message => ({
                        message,
                        wrapper: message.closest('.ui-dialog')
                    }))
                    .filter(item =>
                        visible(item.wrapper) && visible(item.message));
                if (dialogs.length !== 1 ||
                    (dialogs[0].message.textContent || '').trim() !==
                        expectedDialog.text) {
                    return JSON.stringify({ok: false});
                }
            }
            const account = document.querySelector('#LOGIN_ID');
            const password = document.querySelector('#LOGIN_PWD');
            return JSON.stringify({
                ok: true,
                account: account ? account.value : '',
                password: password ? password.value : ''
            });
        })()
