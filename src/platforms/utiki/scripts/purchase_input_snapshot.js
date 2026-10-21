        (() => {
            const visible = element => {
                if (!element || !element.isConnected || element.hidden ||
                    element.closest('[hidden], [inert], [aria-hidden="true"]')) {
                    return false;
                }
                const style = getComputedStyle(element);
                return style.display !== 'none' &&
                    style.visibility !== 'hidden' &&
                    element.getClientRects().length > 0;
            };
            const usable = element => visible(element) && !element.disabled &&
                element.getAttribute('aria-disabled') !== 'true' &&
                !element.readOnly;
            const captcha = Array.from(document.querySelectorAll('input#CHK'));
            const loginIds = Array.from(document.querySelectorAll('#LOGIN_ID'))
                .filter(visible);
            const loginPasswords = Array.from(document.querySelectorAll('#LOGIN_PWD'))
                .filter(visible);
            const loginRequired = loginIds.length > 0 || loginPasswords.length > 0;
            const loginFieldsUsable = loginIds.length === 1 &&
                loginPasswords.length === 1 && usable(loginIds[0]) &&
                usable(loginPasswords[0]);
            const buttons = Array.from(document.querySelectorAll('button#addcart'));
            const dialogs = Array.from(document.querySelectorAll('#dialog-message'));
            return JSON.stringify({
                page_url: location.href,
                document_id: String(performance.timeOrigin),
                captcha_present: captcha.length > 0,
                captcha_unique: captcha.length === 1 && visible(captcha[0]),
                captcha_enabled: captcha.length === 1 && usable(captcha[0]),
                manual_value_ready: captcha.length === 1 &&
                    usable(captcha[0]) && captcha[0].value.trim().length === 4,
                login_required: loginRequired,
                login_fields_usable: loginFieldsUsable,
                login_account_present: loginFieldsUsable &&
                    loginIds[0].value.trim().length > 0,
                login_password_present: loginFieldsUsable &&
                    loginPasswords[0].value.length > 0,
                login_ready: !loginRequired || (
                    loginFieldsUsable &&
                    loginIds[0].value.trim().length > 0 &&
                    loginPasswords[0].value.length > 0),
                button_ready: buttons.length === 1 && usable(buttons[0]),
                dialog_visible: dialogs.some(visible)
            });
        })()
