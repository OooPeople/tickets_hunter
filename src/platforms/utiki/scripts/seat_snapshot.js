
        (() => {
            // __ticketsHunterUtikiSeatSnapshot
            const pageUrl = location.href;
            const documentId = String(performance.timeOrigin);
            const result = (ok, reason, values = {}) => JSON.stringify({
                ok, reason, page_url: pageUrl, document_id: documentId,
                ...values
            });
            const visible = element => {
                if (!element || !element.isConnected ||
                    element.closest('[hidden], [inert], [aria-hidden="true"]')) {
                    return false;
                }
                const style = getComputedStyle(element);
                return style.display !== 'none' &&
                    style.visibility !== 'hidden' &&
                    element.getClientRects().length > 0;
            };
            const normalize = value => String(value || '')
                .replace(/\s+/g, ' ').trim();
            const query = new URLSearchParams(location.search);
            const queryValue = key => {
                const values = query.getAll(key);
                return values.length === 1 ? values[0] : null;
            };
            const performanceId = queryValue('PERFORMANCE_ID');
            const groupId = queryValue('GROUP_ID');
            const priceAreaId = queryValue('PERFORMANCE_PRICE_AREA_ID');
            const token = /^[A-Za-z0-9_-]+$/;
            if (![performanceId, groupId, priceAreaId].every(
                value => typeof value === 'string' && token.test(value))) {
                return result(false, 'identity');
            }
            const exactHidden = (selector, expected) => {
                const inputs = Array.from(document.querySelectorAll(selector));
                return inputs.length === 1 &&
                    (inputs[0].value || '') === expected;
            };
            const productInputs = Array.from(
                document.querySelectorAll('#PRODUCT_ID'));
            const productId = productInputs.length === 1 ?
                (productInputs[0].value || '') : '';
            const limitInputs = Array.from(
                document.querySelectorAll('#QUANTITY_LIMIT'));
            const limitRaw = limitInputs.length === 1 ?
                normalize(limitInputs[0].value) : '';
            if (!exactHidden('#PERFORMANCE_ID', performanceId) ||
                !exactHidden('#GROUP_ID', groupId) ||
                !exactHidden('#PRICE_AREA_ID', priceAreaId) ||
                productInputs.length !== 1 || !token.test(productId) ||
                limitInputs.length !== 1 || !/^\d+$/.test(limitRaw)) {
                return result(false, 'identity');
            }
            const seatRoots = Array.from(document.querySelectorAll('#TBL'));
            const ticketRoots = Array.from(
                document.querySelectorAll('.ticket'));
            if (seatRoots.length !== 1 || !visible(seatRoots[0]) ||
                ticketRoots.length !== 1 || !visible(ticketRoots[0])) {
                return result(false, 'scope');
            }
            if (!window.seats || typeof window.seats !== 'object' ||
                Array.isArray(window.seats)) {
                return result(false, 'state');
            }
            const ticketLabel = button => {
                const clone = button.cloneNode(true);
                clone.querySelectorAll('.checkNum').forEach(node => node.remove());
                return normalize(clone.textContent);
            };
            const ticketTypes = Array.from(ticketRoots[0].querySelectorAll(
                'button[onclick*="setType"]')).map((button, index) => {
                const counters = Array.from(
                    button.querySelectorAll('.checkNum'));
                const counter = counters.length === 1 ? counters[0] : null;
                const text = ticketLabel(button);
                const typeId = counter ? (counter.id || '') : '';
                const typeName = counter ?
                    (counter.getAttribute('val') || '') : '';
                return {
                    fingerprint: [
                        index, button.tagName.toLowerCase(), button.id || '',
                        button.getAttribute('type') || '',
                        button.getAttribute('name') || '',
                        button.getAttribute('value') || '',
                        button.getAttribute('onclick') || '', text,
                        typeId, typeName
                    ],
                    text,
                    disabled: !!button.disabled || !visible(button) ||
                        button.getAttribute('aria-disabled') === 'true',
                    type_id: typeId,
                    type_name: typeName,
                    counter_present: counters.length === 1,
                    counter_raw: counter ? normalize(counter.textContent) : ''
                };
            });
            if (!ticketTypes.length) return result(false, 'not-ready');
            const seatNodes = Array.from(
                seatRoots[0].querySelectorAll('td[title]'));
            const seats = seatNodes.map((seat, index) => {
                const inline = (seat.getAttribute('style') || '').toLowerCase();
                const style = getComputedStyle(seat);
                const background = (style.backgroundImage || '').toLowerCase();
                const selected = inline.includes('icon_chair_select') ||
                    background.includes('icon_chair_select');
                const available = !selected && (
                    inline.includes('seat-empty') ||
                    background.includes('seat-empty') ||
                    style.cursor === 'pointer');
                const rect = seat.getBoundingClientRect();
                const title = normalize(seat.getAttribute('title'));
                const rowIndex = seat.parentElement ? seat.parentElement.rowIndex : -1;
                const cellIndex = seat.cellIndex;
                return {
                    fingerprint: [
                        index, title, rowIndex, cellIndex, seat.id || '',
                        seat.getAttribute('onclick') || '',
                        seat.getAttribute('data-seat-id') || ''
                    ],
                    title,
                    row_index: rowIndex,
                    cell_index: cellIndex,
                    center_x: rect.left + rect.width / 2,
                    center_y: rect.top + rect.height / 2,
                    width: rect.width,
                    height: rect.height,
                    available,
                    selected
                };
            });
            const counters = Array.from(ticketRoots[0].querySelectorAll(
                '.checkNum')).map(counter => ({
                id: counter.id || '',
                counter_raw: normalize(counter.textContent)
            }));
            const submitted = Object.entries(window.seats)
                .filter(([, seat]) => seat && String(seat.A || '') !== '')
                .map(([key, seat]) => ({
                    key,
                    A: String(seat.A || ''),
                    Y: String(seat.Y || ''),
                    Z: String(seat.Z || '')
                }));
            return result(true, '', {
                performance_id: performanceId,
                group_id: groupId,
                price_area_id: priceAreaId,
                product_id: productId,
                product_hidden_present: true,
                quantity_limit_present: true,
                quantity_limit_raw: limitRaw,
                ticket_types: ticketTypes,
                seats,
                submitted_seats: submitted,
                counters,
                visual_selected_count: seats.filter(seat => seat.selected).length
            });
        })()
