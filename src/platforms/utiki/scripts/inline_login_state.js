
        (() => {
            const input = document.querySelector('#LOGIN_ID');
            const password = document.querySelector('#LOGIN_PWD');
            if (!input) return {required: false};
            const style = getComputedStyle(input);
            const required = style.display !== 'none' && style.visibility !== 'hidden' && input.offsetParent !== null;
            return {
                required: required,
                account: input.value || '',
                password: password ? password.value : ''
            };
        })()
