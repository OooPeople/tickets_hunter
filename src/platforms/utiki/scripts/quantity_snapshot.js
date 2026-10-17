
        (() => {
            const roots = Array.from(document.querySelectorAll(
                '[id="table_tickettype"]'));
            const scopedInputs = Array.from(new Set(roots.flatMap(root =>
                Array.from(root.querySelectorAll('input.numbox[key]')))));
            const inputs = Array.from(document.querySelectorAll(
                'input.numbox[key]'));
            if (!roots.length || !inputs.length) {
                return JSON.stringify({
                    ok: false, reason: 'not-ready', page_url: location.href,
                    document_id: String(performance.timeOrigin)
                });
            }
            const scopedSet = new Set(scopedInputs);
            if (inputs.length !== scopedInputs.length ||
                inputs.some(input => !scopedSet.has(input))) {
                return JSON.stringify({
                    ok: false, reason: 'scope', page_url: location.href,
                    document_id: String(performance.timeOrigin)
                });
            }
            const query = new URLSearchParams(location.search);
            const identity = (selector, key) => {
                const values = query.getAll(key);
                if (values.length !== 1) return null;
                const hidden = Array.from(document.querySelectorAll(selector));
                if (hidden.length > 1 ||
                    (hidden.length === 1 &&
                        (hidden[0].value || '') !== values[0])) return null;
                return {value: values[0], present: hidden.length === 1};
            };
            const performanceIdentity = identity(
                '#PERFORMANCE_ID', 'PERFORMANCE_ID');
            const groupIdentity = identity('#GROUP_ID', 'GROUP_ID');
            const priceAreaIdentity = identity(
                '#PERFORMANCE_PRICE_AREA_ID',
                'PERFORMANCE_PRICE_AREA_ID');
            const productInputs = Array.from(
                document.querySelectorAll('#PRODUCT_ID'));
            const productId = productInputs.length === 1 ?
                (productInputs[0].value || '') :
                (productInputs.length ? '__invalid__' : '');
            const limitInputs = Array.from(
                document.querySelectorAll('#QUANTITY_LIMIT'));
            if (performanceIdentity === null || groupIdentity === null ||
                priceAreaIdentity === null || productInputs.length > 1 ||
                (productInputs.length === 1 &&
                    !/^[A-Za-z0-9_-]+$/.test(productId)) ||
                limitInputs.length > 1) {
                return JSON.stringify({
                    ok: false, reason: 'identity', page_url: location.href,
                    document_id: String(performance.timeOrigin)
                });
            }
            const limit = limitInputs[0] || null;
            return JSON.stringify({
                ok: true,
                page_url: location.href,
                document_id: String(performance.timeOrigin),
                performance_id: performanceIdentity.value,
                product_id: productId,
                group_id: groupIdentity.value,
                price_area_id: priceAreaIdentity.value,
                performance_hidden_present: performanceIdentity.present,
                product_hidden_present: productInputs.length === 1,
                group_hidden_present: groupIdentity.present,
                price_area_hidden_present: priceAreaIdentity.present,
                quantity_limit_present: limitInputs.length === 1,
                quantity_limit_raw: limit ? (limit.value || '').trim() : '',
                rows: inputs.map(input => {
                    const row = input.closest('tr') ||
                        input.closest('[id="table_tickettype"]');
                    const remaining = Array.from(
                        row.querySelectorAll('input[key="LAST_AMOUNT"]'));
                    const nameCell = row.querySelector('td.fill');
                    return {
                        key: input.getAttribute('key') || '',
                        name: (nameCell?.textContent || '').trim(),
                        text: (row.innerText || row.textContent || '')
                            .replace(/\s+/g, ' ').trim(),
                        disabled: !!input.disabled ||
                            input.getAttribute('aria-disabled') === 'true',
                        remaining_present: remaining.length > 0,
                        remaining_raw: remaining.length === 0 ? '' :
                            remaining.length === 1 ?
                                (remaining[0].value || '').trim() : '__invalid__',
                        value: (input.value || '').trim()
                    };
                })
            });
        })()
