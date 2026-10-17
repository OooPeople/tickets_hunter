
        (() => {
            // __ticketsHunterUtikiDialogSnapshot
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
            return JSON.stringify({
                page_url: location.href,
                document_id: String(performance.timeOrigin),
                visible_count: dialogs.length,
                text: dialogs.length === 1 ?
                    (dialogs[0].message.textContent || '').trim() : ''
            });
        })()
