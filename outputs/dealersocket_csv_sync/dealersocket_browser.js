async (page) => {
    // Read-only extraction from the page's currently loaded Angular components.
    // No API endpoints, credentials, login forms, save buttons or contact actions.
    const cfg = __CONFIG__;
    const isDealerSocket = p => {
        try { return new URL(p.url()).hostname === 'bb.dealersocket.com'; }
        catch { return false; }
    };
    const isOpportunities = p => isDealerSocket(p)
        && /^#\/crm\/employee\?/.test(new URL(p.url()).hash)
        && new URLSearchParams(new URL(p.url()).hash.split('?')[1]).get('todoType') === 'opportunities';
    const pages = page.context().pages().filter(p => !p.isClosed());
    const target = (isOpportunities(page) && page) || pages.find(isOpportunities)
        || (isDealerSocket(page) && page) || pages.find(isDealerSocket);
    if (!target) throw new Error('Share the signed-in DealerSocket tab with this MCP client in Chrome.');
    if (!isOpportunities(target)) {
        await target.goto('https://bb.dealersocket.com/#/crm/employee?todoType=opportunities',
            { waitUntil: 'domcontentloaded', timeout: 30000 });
    } else if (cfg.op === 'filter' && cfg.recover) {
        // A timed-out selection may not fire another Angular change for the same value.
        // Reload only on the single explicit filter retry; never reload customer forms.
        await target.reload({ waitUntil: 'domcontentloaded', timeout: 30000 });
    }
    // browser_tabs/new can finish before the Angular shell creates the CRM frame.
    // Match the component, not a case-sensitive or version-specific iframe URL.
    const listSelector = 'crm-employee-dashboard-list-details';
    const started = Date.now();
    let frame;
    while (Date.now() - started < 55000) {
        if (target.isClosed()) throw new Error('The DealerSocket automation tab was closed. Keep it open during the scan.');
        for (const candidate of target.frames()) {
            try {
                const ready = await candidate.locator(listSelector).evaluate(e => {
                    const c = window.angular?.element(e).controller('crmEmployeeDashboardListDetails');
                    return !!(c?.dealerships?.length && c.selectedDealership && c.selectedFilter
                        && Array.isArray(c.categoryFilters));
                }, undefined, { timeout: 500 });
                if (ready) { frame = candidate; break; }
            } catch { /* Frame creation/navigation and component initialization are asynchronous. */ }
        }
        if (frame) break;
        try {
            if (Date.now() - started > 5000 && await target.locator('input[type="password"]').first().isVisible())
                throw new Error('DealerSocket is showing a sign-in form. Sign in to this Chrome profile, then retry.');
        } catch (error) {
            if (String(error.message).includes('showing a sign-in form')) throw error;
        }
        await target.waitForTimeout(250);
    }
    if (!frame) throw new Error('DealerSocket opened, but its Opportunities component was not ready within 55 seconds. '
        + 'Keep the automation tab open and retry --diagnose after the page finishes loading. '
        + 'This is a page-readiness failure, not evidence that you need a new extension token.');
    const ua = await target.evaluate(() => navigator.userAgent);
    if (!ua.includes('Chrome/') || ua.includes('Edg/')) throw new Error('Use the extension token from Google Chrome, not another browser.');
    const list = frame.locator(listSelector);
    const readList = async fn => list.evaluate(fn);
    const listView = async () => {
        for (let i = 0; i < 3; i++) {
            if (await frame.locator('select[name="filter-dropdown"]').isVisible()) return;
            const expand = frame.locator('button[name="expandButton"]');
            if (!await expand.isVisible()) break;
            await expand.click();
        }
        await frame.locator('select[name="filter-dropdown"]').waitFor({ timeout: 10000 });
    };
    const ready = async (siteId, filterId = null) => {
        try {
            await frame.waitForFunction(({ siteId, filterId }) => {
                const e = document.querySelector('crm-employee-dashboard-list-details');
                const c = e && window.angular?.element(e).controller('crmEmployeeDashboardListDetails');
                return c && !c.isLoadingTodos && String(c.selectedDealership?.value) === String(siteId)
                    && String(c.selectedFilter?.siteId) === String(siteId)
                    && c.categoryFilters?.length > 0
                    && c.categoryFilters.every(x => String(x.siteId) === String(siteId))
                    && (filterId === null || String(c.selectedFilter?.id) === String(filterId));
            }, { siteId, filterId }, { timeout: 30000 });
        } catch (error) {
            const observed = await readList(e => {
                const c = window.angular?.element(e).controller('crmEmployeeDashboardListDetails');
                return {
                    site: c?.selectedDealership?.value, filter: c?.selectedFilter?.id,
                    filterSite: c?.selectedFilter?.siteId, busy: c?.isLoadingTodos,
                    loadedRows: c?.todos?.length, filterCount: c?.categoryFilters?.length
                };
            }).catch(() => ({ component: 'unavailable' }));
            throw new Error('Filter readiness timeout: expected site=' + siteId + ' filter=' + filterId
                + '; observed=' + JSON.stringify(observed) + '; ' + String(error.message).split('\n')[0]);
        }
        // Wait for deferred Angular requests, not just the selection's immediate change.
        let previous = '', stable = 0;
        for (let i = 0; i < 40; i++) {
            const s = await readList(e => {
                const c = angular.element(e).controller('crmEmployeeDashboardListDetails');
                return {
                    busy: !!c.isLoadingTodos, site: String(c.selectedDealership.value),
                    filter: String(c.selectedFilter.id), filterSite: String(c.selectedFilter.siteId),
                    ids: (c.todos || []).map(x => [x.dealershipId, x.opportunityType, x.salesOpportunityId, x.contactId, x.activityId]).join('|')
                };
            });
            const key = JSON.stringify(s);
            stable = (!s.busy && s.site === String(siteId) && s.filterSite === String(siteId)
                && (filterId === null || s.filter === String(filterId)) && key === previous) ? stable + 1 : 0;
            if (stable >= 2) return;
            previous = key;
            await frame.waitForTimeout(250);
        }
        throw new Error('Result list did not settle; this filter is not marked complete.');
    };
    const meta = async () => readList(e => {
        const c = angular.element(e).controller('crmEmployeeDashboardListDetails');
        if (!c) throw new Error('DealerSocket page component changed; extraction adapter requires an update.');
        return {
            sites: c.dealerships.map(x => ({ id: String(x.value), name: x.text })),
            site: { id: String(c.selectedDealership.value), name: c.selectedDealership.text },
            filter: { id: String(c.selectedFilter.id), name: c.selectedFilter.name },
            user: c.selectedDealershipUser?.text || '', keyword: c.searchText || '',
            filters: c.categoryFilters.map(x => ({
                id: String(x.id), name: x.name, category: x.filterCategory,
                site_id: String(x.siteId), is_my_filter: !!x.isMyFilter, is_enterprise: !!x.isEnterprise
            }))
        };
    });
    if (cfg.op === 'discover') {
        await listView();
        const m = await meta(); await ready(m.site.id);
        return { ok: true, ...await meta() };
    }
    if (cfg.op === 'site') {
        await listView();
        const option = await frame.locator('select[name="expanded-dealership-select"] option').evaluateAll(
            (es, name) => es.filter(x => x.text === name).map(x => x.value), cfg.name);
        if (option.length !== 1) throw new Error('Dealership option unavailable or ambiguous: ' + cfg.name);
        await frame.locator('select[name="expanded-dealership-select"]').selectOption(option[0]);
        await ready(cfg.site_id);
        return { ok: true, ...await meta() };
    }
    if (cfg.op === 'filter') {
        await listView();
        const option = await list.evaluate((e, id) => {
            const c = angular.element(e).controller('crmEmployeeDashboardListDetails');
            const item = c.categoryFilters.find(x => String(x.id) === String(id));
            if (!item) throw new Error('Filter no longer available: ' + id);
            return item.$$hashKey;
        }, cfg.filter_id);
        await frame.locator('select[name="filter-dropdown"]').selectOption(option);
        await ready(cfg.site_id, cfg.filter_id);
        const result = await readList(e => {
            const c = angular.element(e).controller('crmEmployeeDashboardListDetails');
            const keys = ['dealershipId', 'activityId', 'franchiseId', 'contactId', 'salesOpportunityId', 'fullName',
                'opportunityType', 'eventStatus', 'franchiseName', 'condition', 'year', 'make', 'model', 'assignedTo',
                'activityDateTime', 'activityMood', 'description', 'lastUpdate', 'age'];
            const todos = (c.todos || []).map(x => Object.fromEntries(keys.map(k => [k, x[k] ?? null])));
            const labels = [...e.querySelectorAll('[name="results-display"]')]
                .map(x => (x.textContent || '').trim()).filter(x => /^\d[\d,]* RESULTS? FOUND$/i.test(x));
            const displayed = labels.length ? Number(labels[0].replace(/[^0-9]/g, '')) : null;
            return { todos, displayed_count: displayed, loaded_count: todos.length, keyword: c.searchText || '' };
        });
        if (result.keyword) throw new Error('Keyword filter is active. Clear it before a complete scan.');
        if (result.displayed_count === null) throw new Error('Displayed result count is unavailable; refusing to certify list coverage.');
        if (result.displayed_count !== null && result.displayed_count !== result.loaded_count)
            throw new Error('Loaded rows do not match the displayed result count; cannot certify this filter.');
        return { ok: true, ...result };
    }
    if (cfg.op === 'detail') {
        const todo = cfg.todo;
        const key = x => [String(x.dealershipId), String(x.opportunityType).toLowerCase(), String(x.salesOpportunityId), String(x.contactId)].join('|');
        const expected = key(todo);
        const matchesCard = (e, expected) => {
            const x = window.angular?.element(e).controller('crmOpportunityCard')?.opportunity;
            return !!x && [String(x.dealershipId), String(x.opportunityType).toLowerCase(),
            String(x.salesOpportunityId), String(x.contactId)].join('|') === expected;
        };
        const matchesDetail = ({ id, contact, type }) => {
            const angular = window.angular;
            const view = document.querySelector('crm-employee-dashboard-opportunity-view');
            if (!view || !angular) return false;
            const bindings = [['sales', 'crm-sales-opportunity-detail-view', 'crmSalesOpportunityDetailView', 'salesOpportunity', 'contact'],
            ['service', 'ds-service-opportunity-container', 'dsServiceOpportunityContainer', 'serviceOpportunity', 'owner'],
            ['qa', 'csi-update', 'csiUpdate', 'csiEvent', 'contact']];
            for (const [kind, tag, name, model, p] of bindings) {
                if (kind !== String(type).toLowerCase()) continue;
                const e = view.querySelector(tag); if (!e) continue;
                const c = angular.element(e).controller(name);
                const actual = c?.[model]?.id || c?.opportunityId || c?.serviceOpportunityId;
                const customer = c?.[p]?.id;
                const loading = c?.isLoading || c?.numberItemsStillLoading > 0;
                if (String(actual) === String(id) && String(customer) === String(contact) && !loading) return true;
            }
            return false;
        };
        const detailIdentity = { id: todo.salesOpportunityId, contact: todo.contactId, type: todo.opportunityType };
        const maxAttempts = Math.min(3, Math.max(1, Number(cfg.max_attempts) || 3));
        let opened = false, lastError = 'No matching visible card';
        for (let attempt = 1; attempt <= maxAttempts && !opened; attempt++) {
            try {
                await listView();
                const index = await list.evaluate((e, { expected, siteId, filterId }) => {
                    const c = angular.element(e).controller('crmEmployeeDashboardListDetails');
                    if (c.isLoadingTodos || (siteId && String(c.selectedDealership?.value) !== String(siteId))
                        || (filterId && String(c.selectedFilter?.id) !== String(filterId)))
                        throw new Error('Filter changed or is still loading; refusing to select a different record.');
                    return c.todos.findIndex(x => [String(x.dealershipId), String(x.opportunityType).toLowerCase(),
                    String(x.salesOpportunityId), String(x.contactId)].join('|') === expected);
                }, { expected, siteId: cfg.site_id, filterId: cfg.filter_id });
                if (index < 0) throw new Error('Record disappeared from the current filter; rerun this filter.');
                await list.evaluate((e, index) => {
                    const host = e.querySelector('md-virtual-repeat-container');
                    const virtual = host && angular.element(host).data().$mdVirtualRepeatContainerController;
                    if (!virtual || typeof virtual.scrollToIndex !== 'function')
                        throw new Error('DealerSocket virtual-list controller unavailable.');
                    virtual.scrollToIndex(index);
                }, index);
                let clicked = false;
                for (let poll = 0; poll < 12 && !clicked; poll++) {
                    await frame.waitForTimeout(150);
                    // Never cache nth(): virtual scrolling recycles/reorders these elements.
                    const cards = await list.locator('crm-opportunity-card').elementHandles();
                    try {
                        for (const card of cards) {
                            if (!await card.evaluate(matchesCard, expected)) continue;
                            const controls = await card.$$('[ng-click="ctrl.select()"]');
                            try {
                                for (const control of controls) {
                                    if (!await control.isVisible()) continue;
                                    await control.scrollIntoViewIfNeeded({ timeout: 1500 });
                                    if (!await card.evaluate(matchesCard, expected)) break;
                                    await control.click({ timeout: 2500 });
                                    clicked = true; break;
                                }
                            } catch (error) {
                                // Let clipped/recycled-row failures reach the verified fallback.
                                if (!/Timeout|detached|not attached/i.test(String(error?.name) + ' ' + String(error?.message))) throw error;
                                lastError = String(error.message).split('\n')[0];
                            } finally { await Promise.all(controls.map(x => x.dispose())); }
                            if (!clicked) {
                                // The virtual list can recycle the correct card while all click
                                // targets are clipped. Re-verify both IDs before selecting it.
                                clicked = await card.evaluate((e, expected) => {
                                    const c = window.angular?.element(e).controller('crmOpportunityCard');
                                    const x = c?.opportunity;
                                    const actual = [String(x?.dealershipId), String(x?.opportunityType).toLowerCase(),
                                        String(x?.salesOpportunityId), String(x?.contactId)].join('|');
                                    if (actual !== expected || typeof c?.onSelect !== 'function') return false;
                                    c.onSelect({ driver: undefined });
                                    return true;
                                }, expected);
                            }
                            if (clicked) break;
                        }
                    } finally { await Promise.all(cards.map(x => x.dispose())); }
                }
                if (!clicked) throw new Error('Virtual row has no matching visible selection control: ' + expected);
                await frame.waitForFunction(matchesDetail, detailIdentity, { timeout: Math.min(12000, Math.max(1000, Number(cfg.detail_timeout) || 12000)) });
                opened = true;
            } catch (error) {
                lastError = String(error.message).split('\n')[0];
            }
        }
        if (!opened) throw new Error('Could not open and verify ' + expected + ' after ' + maxAttempts + ' attempts: ' + lastError);
        // The timeline loads independently from the contact/opportunity header.
        let timelineState = 'not_available';
        if (await frame.locator('crm-employee-dashboard-opportunity-view crm-activity-timeline').count()) {
            try {
                await frame.waitForFunction(contact => {
                    const e = document.querySelector('crm-employee-dashboard-opportunity-view crm-activity-timeline');
                    const c = e && angular.element(e).controller('crmActivityTimeline');
                    return c && c.isWaiting === false && String(c.contact?.id) === String(contact);
                }, todo.contactId, { timeout: 10000 });
                timelineState = 'ready';
            } catch { timelineState = 'timeout'; }
        }
        await frame.waitForTimeout(500);
        const record = await frame.locator('crm-employee-dashboard-opportunity-view').evaluate((view, todo) => {
            const get = (tag, name) => { const e = view.querySelector(tag); return e && angular.element(e).controller(name); };
            const sales = get('crm-sales-opportunity-detail-view', 'crmSalesOpportunityDetailView');
            const service = get('ds-service-opportunity-container', 'dsServiceOpportunityContainer');
            const qa = get('csi-update', 'csiUpdate');
            const c = sales || service || qa;
            const s = sales?.salesOpportunity || service?.serviceOpportunity || qa?.csiEvent || {};
            const p = c?.contact || c?.owner || {};
            const actual = s.id || c?.opportunityId || c?.serviceOpportunityId;
            if (String(actual) !== String(todo.salesOpportunityId) || String(p.id) !== String(todo.contactId))
                throw new Error('Detail identity changed before extraction; refusing to export the wrong customer.');
            if (p.isRestricted || s.restricted) throw new Error('This record is restricted for the signed-in user; details were not exported.');
            const value = x => x == null ? '' : typeof x === 'object' ? String(x.text ?? x.value ?? '') : String(x);
            const cp = p.contactPoints || {};
            const mobile = value(cp.savedMobile?.value || cp._mobile?.value || cp.mobile?.value || '');
            const notes = [...view.querySelectorAll('[name="activityWorkNoteText"]')].map(x => x.innerText || '');
            const submitted = notes.find(n => /Information from the Submitted Lead/i.test(n)) || '';
            const sourceLine = submitted.match(/(?:^|\n)Lead Source:\s*([^\n]+)/i)?.[1] || '';
            const vehicles = s.vehiclesOfInterest || [s.vehicle].filter(Boolean);
            const vehicle = vehicles.map(v => [v.year, v.make, v.model, v.trim].filter(Boolean).join(' ')).filter(Boolean).join('; ')
                || [todo.year, todo.make, todo.model].filter(Boolean).join(' ');
            const status = value(s.status) || s.statusText || todo.eventStatus || '';
            const franchise = value(s.franchise) || todo.franchiseName || '';
            const created = s.createdDate || '';
            const contactId = p.id || todo.contactId;
            return {
                opportunity_id: String(todo.salesOpportunityId || ''), customer_id: String(contactId || ''),
                opportunity_type: String(todo.opportunityType || ''),
                dealership_id: String(s.dealershipId || s.dealership?.key || todo.dealershipId || ''),
                rooftop: franchise, franchise_id: String(s.franchise?.key || todo.franchiseId || ''),
                customer_name: p.fullName || todo.fullName || '', customer_phone_raw: mobile,
                customer_email_raw: value(cp.email?.value), vehicle_of_interest: vehicle,
                lead_source: sourceLine || value(s.trackingCode) || value(s.source),
                lead_source_basis: sourceLine ? 'submitted_lead_note' : value(s.trackingCode) ? 'tracking_code' : 'opportunity_source',
                source_type: value(s.source), tracking_code: value(s.trackingCode),
                assigned_salesperson: value(s.primaryAssigned || s.advisor || s.assignTo) || todo.assignedTo || '',
                assigned_salesperson_id: String(s.primaryAssigned?.value || s.advisor?.value || s.assignTo?.value || ''),
                received_timestamp: created, received_timestamp_basis: created ? 'opportunity.createdDate' : 'unavailable',
                last_updated_timestamp: s.lastUpdatedOrSoldDate || s.lastUpdate || '',
                opportunity_status: status, description: s.description || todo.description || '',
                customer_comments: submitted.match(/Customer Comments:\s*([\s\S]*?)(?:Information from the Submitted Lead:|$)/i)?.[1]?.trim() || '',
                customer_comments_basis: submitted ? 'loaded_submitted_lead_note' : 'unavailable_in_loaded_timeline',
                never_contact_via: value(p.neverContactVia),
                detail_extracted: true, adapter: sales ? 'sales' : service ? 'service' : 'qa',
                model_id_present: !!s.id
            };
        }, todo);
        if (record.customer_id !== String(todo.contactId)) throw new Error('Customer identity mismatch; refusing stale record.');
        record.source_url = target.url();
        record.timeline_load_status = timelineState;
        return { ok: true, record };
    }
    if (cfg.op === 'restore') {
        await listView();
        await frame.locator('select[name="expanded-dealership-select"]').selectOption({ label: cfg.site_name });
        await ready(cfg.site_id);
        const option = await list.evaluate((e, id) => angular.element(e).controller('crmEmployeeDashboardListDetails')
            .categoryFilters.find(x => String(x.id) === String(id))?.$$hashKey, cfg.filter_id);
        if (option) { await frame.locator('select[name="filter-dropdown"]').selectOption(option); await ready(cfg.site_id, cfg.filter_id); }
        return { ok: true };
    }
    throw new Error('Unknown operation: ' + cfg.op);
}
