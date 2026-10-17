
        (() => {
            const expectedOrigin = __UTIKI_INLINE_LOGIN_FILL_ARG_0__;
            const currentUrl = new URL(location.href);
            if (
                currentUrl.origin !== expectedOrigin ||
                currentUrl.username ||
                currentUrl.password
            ) return false;
            const account = document.querySelector('#LOGIN_ID');
            const password = document.querySelector('#LOGIN_PWD');
            if (!account || !password) return false;
            account.value = __UTIKI_INLINE_LOGIN_FILL_ARG_1__;
            password.value = __UTIKI_INLINE_LOGIN_FILL_ARG_2__;
            account.dispatchEvent(new Event('input', {bubbles: true}));
            password.dispatchEvent(new Event('input', {bubbles: true}));
            return true;
        })()
