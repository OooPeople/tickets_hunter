
        (() => {
            // __ticketsHunterUtikiDialogDismiss
            const snapshot = __UTIKI_DIALOG_DISMISS_ARG_0__;
            if (location.href !== snapshot.pageUrl ||
                String(performance.timeOrigin) !== snapshot.documentId) {
                return 'stale';
            }
            const visible = element => {
                if (!element || !element.isConnected || element.hidden ||
                    element.getAttribute('aria-hidden') === 'true') return false;
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
                    snapshot.text) return 'stale';
            const buttons = Array.from(dialogs[0].wrapper.querySelectorAll(
                '.ui-dialog-buttonset button')).filter(visible);
            if (buttons.length !== 1 || buttons[0].disabled ||
                buttons[0].getAttribute('aria-disabled') === 'true') {
                return 'rejected';
            }
            buttons[0].click();
            return 'committed';
        })()
