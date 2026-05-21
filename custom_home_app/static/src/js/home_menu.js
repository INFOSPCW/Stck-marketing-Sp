import { hasTouch, isIosApp } from "@web/core/browser/feature_detection";
import { user } from "@web/core/user";
import { session } from "@web/session";
import { useService } from "@web/core/utils/hooks";
import { patch } from "@web/core/utils/patch";
import { fuzzyLookup } from "@web/core/utils/search";
import { computeAppsAndMenuItems } from "@web/webclient/menus/menu_helpers";
import { registry } from "@web/core/registry";
import { KeepLast } from "@web/core/utils/concurrency";
import { WebClient } from "@web/webclient/webclient";
import { NavBar } from "@web/webclient/navbar/navbar";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";
import {
    Component,
    onMounted,
    onPatched,
    onWillUnmount,
    onWillUpdateProps,
    useExternalListener,
    useRef,
    useState,
} from "@odoo/owl";

const APP_CANVAS_MIN_HEIGHT = 520;
const APP_DRAG_THRESHOLD = 10;
const APP_CLICK_SUPPRESSION_DELAY = 300;

export class CustomHomeMenu extends Component {
    setup() {
        this.command = useService("command");
        this.menus = useService("menu");
        this.ui = useService("ui");
        this.actionService = useService("action");
        this.orm = useService("orm");
        this.state = useState({
            focusedIndex: null,
            isIosApp: isIosApp(),
            timeFormat: "24",
            clockDesign: "classic",
            timeObj: this._getTimeObj(),
            searchValue: "",
            searchResults: [],
            searchResultIndex: 0,
            isSearchDropdownOpen: false,
            isEditMode: !this.env.isSmall,
            homeMenuLayout: this._cloneLayout(user.settings?.home_menu_layout || {}),
            canvasHeight: APP_CANVAS_MIN_HEIGHT,
            draggedAppXmlid: null,
            companyColor: this.colorMapping,
        });
        this.colorMapping = {
            0: "#714B67", // Default Purple
            1: "#F06050", // Red
            2: "#F4A460", // Orange
            3: "#F7CD1F", // Yellow
            4: "#6CC1ED", // Light blue
            5: "#814968", // Dark purple
            6: "#EB7E7F", // Salmon pink
            7: "#2C8397", // Medium blue
            8: "#475577", // Dark blue
            9: "#D6145F", // Fuchsia
            10: "#30C381", // Green
            11: "#9365B8", // Purple
        };
        this.inputRef = useRef("input");
        this.rootRef = useRef("root");
        this.appsCanvasRef = useRef("apps_canvas");
        this.searchContainerRef = useRef("search_container");
        this.boundOnAppPointerMove = this._onAppPointerMove.bind(this);
        this.boundOnAppPointerUp = this._onAppPointerUp.bind(this);
        this.dragState = null;
        this.justDraggedUntil = 0;
        this.keepLast = new KeepLast();

        useExternalListener(window, "resize", () => this._syncAppLayout());
        useExternalListener(window, "keydown", this._onWindowKeydown);

        onWillUpdateProps(() => {
            this.state.focusedIndex = null;
        });

        onMounted(async () => {
            document.body.classList.add("o_home_menu_background");
            if (!hasTouch() && this.inputRef.el) {
                this.inputRef.el.focus();
            }
            this._syncSearchResults();
            this._syncAppLayout();

            try {
                this.state.timeFormat = await this.orm.call("ir.config_parameter", "get_param", ["custom_home_app.time_format", "24"]);
                this.state.clockDesign = await this.orm.call("ir.config_parameter", "get_param", ["custom_home_app.clock_design", "classic"]);
                const companyId = user.defaultCompany.id;
                if (companyId) {
                    const companyData = await this.orm.read("res.company", [companyId], ["color"]);
                    console.log("COMPANY DATA FROM DB:", companyData); // Aa check karo console ma
                    if (companyData && companyData.length > 0) {
                        const colorIdx = companyData[0].color || 0;
                        console.log("COLOR INDEX:", colorIdx); // Su aa '1' (Red) aave che?
                        this.state.companyColor = this.colorMapping[colorIdx] || "#714B67";
                        console.log("FINAL HEX COLOR:", this.state.companyColor);
                    }
                }
            } catch (e) {
                console.error("Could not load clock config", e);
            }
            this.state.timeObj = this._getTimeObj();

            this.clockInterval = setInterval(() => {
                this.state.timeObj = this._getTimeObj();
            }, 1000);
        });

        onPatched(() => {
            if (this.state.focusedIndex !== null && !this.env.isSmall) {
                const selectedItem = document.querySelector(".o_home_menu .o_menuitem.o_focused");
                if (selectedItem) {
                    selectedItem.scrollIntoView({ block: "center" });
                }
            }
            if (!this.dragState) {
                this._syncAppLayout();
            }
        });

        onWillUnmount(() => {
            document.body.classList.remove("o_home_menu_background");
            if (this.clockInterval) {
                clearInterval(this.clockInterval);
            }
            this._cleanupDrag();
        });
    }

    get displayedApps() {
        return this.menus.getApps().map(app => {
            if (app.webIconData && !app.webIconData.startsWith("data:image") && !app.webIconData.startsWith("/")) {
                const prefix = app.webIconData.startsWith("P") ? "data:image/svg+xml;base64," : "data:image/png;base64,";
                app.webIconData = prefix + app.webIconData.replace(/\s/g, "");
            }
            if (app.webIconData === "/web_enterprise/static/img/default_icon_app.png") {
                app.webIconData = "/web/static/img/odoo_logo_tiny.png";
            }
            return app;
        });
    }

    onAppClick(app) {
        if (Date.now() < this.justDraggedUntil) {
            return;
        }
        this._openMenu(app);
    }

    _openMenu(app) {
        this.menus.selectMenu(app);
    }

    _toggleEditMode() {
        this.state.isEditMode = !this.state.isEditMode;
    }

    async _saveLayoutAndExitEdit() {
        this.state.isEditMode = false;
        await this._saveHomeMenuLayout();
    }

    _cloneLayout(layout) {
        return JSON.parse(JSON.stringify(layout || {}));
    }

    _getTimeObj() {
        const now = new Date();
        const is12Hour = this.state?.timeFormat === '12';
        let hours = now.getHours();
        let ampm = "";

        if (is12Hour) {
            ampm = hours >= 12 ? ' PM' : ' AM';
            hours = hours % 12 || 12;
        }

        return {
            hours: hours.toString().padStart(2, '0'),
            minutes: now.getMinutes().toString().padStart(2, '0'),
            seconds: now.getSeconds().toString().padStart(2, '0'),
            ampm: ampm,
            day: now.toLocaleDateString('en-US', { weekday: 'long' }).toUpperCase(),
            raw: now.toLocaleTimeString([], {
                hour: "2-digit",
                minute: "2-digit",
                second: "2-digit",
                hour12: is12Hour
            })
        };
    }

    _getLayoutConstants() {
        const width = window.innerWidth;
        if (width < 576) { // MOBILE
            return { cardW: 70, cardH: 95, gap: 10, padding: 15, columns: 3 };
        } else if (width < 992) { // TABLET
            return { cardW: 80, cardH: 105, gap: 15, padding: 20, columns: 6 };
        } else { // DESKTOP
            return { cardW: 85, cardH: 110, gap: 20, padding: 24, columns: 8 };
        }
    }

    _getCanvasWidth() {
        return this.appsCanvasRef.el?.clientWidth || window.innerWidth;
    }

    _pixelsToGrid(x, y, constants) {
        const row = Math.round((y - constants.padding) / (constants.cardH + constants.gap));
        const totalApps = this.displayedApps.length;
        const totalRows = Math.ceil(totalApps / constants.columns);

        let itemsInRow = constants.columns;
        if (row >= totalRows - 1) {
            itemsInRow = totalApps % constants.columns || (totalApps > 0 ? constants.columns : 0);
        }

        const rowWidth = (itemsInRow * constants.cardW) + ((itemsInRow - 1) * constants.gap);
        const canvasWidth = this._getCanvasWidth();
        const offsetX = Math.max(constants.padding, (canvasWidth - rowWidth) / 2);
        const col = Math.round((x - offsetX) / (constants.cardW + constants.gap));
        return { col: Math.max(0, col), row: Math.max(0, row) };
    }

    _gridToPixels(col, row, constants, itemsInRow) {
        const canvasWidth = this._getCanvasWidth();
        const numIcons = itemsInRow || constants.columns;
        const rowWidth = (numIcons * constants.cardW) + ((numIcons - 1) * constants.gap);
        const offsetX = Math.max(constants.padding, (canvasWidth - rowWidth) / 2);
        return {
            x: offsetX + col * (constants.cardW + constants.gap),
            y: constants.padding + row * (constants.cardH + constants.gap)
        };
    }

    _getDefaultAppPosition(index) {
        const constants = this._getLayoutConstants();
        const col = index % constants.columns;
        const row = Math.floor(index / constants.columns);
        const totalApps = this.displayedApps.length;
        const totalRows = Math.ceil(totalApps / constants.columns);
        let itemsInRow = constants.columns;
        if (row === totalRows - 1) {
            itemsInRow = totalApps % constants.columns || (totalApps > 0 ? constants.columns : 0);
        }
        return this._gridToPixels(col, row, constants, itemsInRow);
    }

    _syncAppLayout() {
        const canvas = this.appsCanvasRef.el;
        if (!canvas) return;
        const constants = this._getLayoutConstants();
        const currentLayout = this.state.homeMenuLayout || {};
        const nextLayout = {};
        const appsWithRanks = this.displayedApps.map(app => {
            const pos = currentLayout[app.xmlid];
            let rank = 999999;
            if (pos) {
                if (pos.col !== undefined) {
                    rank = pos.row * 100 + pos.col;
                } else {
                    const guessed = this._pixelsToGrid(pos.x, pos.y, constants);
                    rank = guessed.row * 100 + guessed.col;
                }
            }
            return { app, rank };
        });
        appsWithRanks.sort((a, b) => a.rank - b.rank);

        let currentSlot = 0;
        const totalApps = appsWithRanks.length;
        const totalRows = Math.ceil(totalApps / constants.columns);

        for (const item of appsWithRanks) {
            const col = currentSlot % constants.columns;
            const row = Math.floor(currentSlot / constants.columns);

            let itemsInRow = constants.columns;
            if (row === totalRows - 1) {
                itemsInRow = totalApps % constants.columns || (totalApps > 0 ? constants.columns : 0);
            }

            const pixels = this._gridToPixels(col, row, constants, itemsInRow);
            nextLayout[item.app.xmlid] = { col, row, x: pixels.x, y: pixels.y };
            currentSlot++;
        }
        if (JSON.stringify(nextLayout) !== JSON.stringify(currentLayout)) {
            this.state.homeMenuLayout = nextLayout;
        }
        this.state.canvasHeight = this._computeCanvasHeight(nextLayout);
    }

    _computeCanvasHeight(layout) {
        const constants = this._getLayoutConstants();
        let maxRow = 0;
        Object.values(layout).forEach(pos => {
            if (pos.row > maxRow) maxRow = pos.row;
        });
        const height = (maxRow + 1) * (constants.cardH + constants.gap) + constants.padding * 2;
        return Math.max(APP_CANVAS_MIN_HEIGHT, height);
    }

    _getAppAtPosition(x, y, excludeXmlid) {
        const constants = this._getLayoutConstants();
        const thresholdX = constants.cardW / 2;
        const thresholdY = constants.cardH / 2;
        return Object.entries(this.state.homeMenuLayout).find(([xmlid, pos]) => {
            return xmlid !== excludeXmlid &&
                Math.abs(pos.x - x) < thresholdX &&
                Math.abs(pos.y - y) < thresholdY;
        });
    }

    _serializeLayout() {
        const layout = {};
        for (const app of this.displayedApps) {
            const pos = this.state.homeMenuLayout[app.xmlid];
            if (pos) {
                layout[app.xmlid] = { col: pos.col, row: pos.row, x: Math.round(pos.x), y: Math.round(pos.y) };
            }
        }
        return layout;
    }

    async _saveHomeMenuLayout() {
        await user.setUserSettings("home_menu_layout", this._serializeLayout());
    }

    async _sortApps(mode) {
        if (mode === 'reset' || mode === 'az' || mode === 'za') {
            const sortedApps = [...this.displayedApps].sort((a, b) => {
                const labelA = (a.label || a.name || "").toLowerCase();
                const labelB = (b.label || b.name || "").toLowerCase();
                if (mode === 'reset') return this.displayedApps.indexOf(a) - this.displayedApps.indexOf(b);
                return mode === 'az' ? labelA.localeCompare(labelB) : labelB.localeCompare(labelA);
            });
            const constants = this._getLayoutConstants();
            const newLayout = {};
            const totalApps = sortedApps.length;
            const totalRows = Math.ceil(totalApps / constants.columns);

            sortedApps.forEach((app, index) => {
                const col = index % constants.columns;
                const row = Math.floor(index / constants.columns);
                let itemsInRow = constants.columns;
                if (row === totalRows - 1) {
                    itemsInRow = totalApps % constants.columns || (totalApps > 0 ? constants.columns : 0);
                }
                const pixels = this._gridToPixels(col, row, constants, itemsInRow);
                newLayout[app.xmlid] = { col, row, x: pixels.x, y: pixels.y };
            });
            this.state.homeMenuLayout = newLayout;
            await this._saveHomeMenuLayout();
            this.state.canvasHeight = this._computeCanvasHeight(newLayout);
        }
    }

    async _buildSearchResults(searchValue) {
        if (!searchValue) return [];
        const { apps, menuItems } = computeAppsAndMenuItems(this.menus.getMenuAsTree("root"));
        const results = [];

        fuzzyLookup(searchValue, apps, (menu) => menu.label).slice(0, 5).forEach((menu) => {
            results.push({ key: `app:${menu.xmlid}`, label: menu.label, parents: "", type: "app", menu });
        });

        const providers = registry.category("command_provider").getAll();
        const proms = providers.map(p => {
            try {
                return p.provide(this.env, { searchValue, activeElement: this.ui.activeElement });
            } catch (e) {
                return [];
            }
        });
        const providerResults = (await Promise.all(proms)).flat();

        providerResults.slice(0, 10).forEach(cmd => {
            if (!results.some(r => r.label === cmd.name)) {
                results.push({
                    key: `cmd:${cmd.name}_${Math.random()}`,
                    label: cmd.name,
                    parents: cmd.category || "",
                    type: cmd.category || 'command',
                    action: cmd.action,
                    menu: cmd,
                    href: cmd.href
                });
            }
        });

        if (results.length < 8) {
            fuzzyLookup(searchValue, menuItems, (menu) => `${menu.parents} / ${menu.label}`.split("/").reverse().join("/"))
                .slice(0, 8 - results.length)
                .forEach((menu) => {
                    results.push({ key: `menu:${menu.id}`, label: menu.label, parents: menu.parents, type: "menu", menu });
                });
        }
        return results;
    }

    async _syncSearchResults() {
        const value = this.inputRef.el ? this.inputRef.el.value : this.state.searchValue;
        const searchValue = value.trim();
        this.state.searchValue = value;
        if (!searchValue) {
            this.state.searchResults = [];
            this.state.isSearchDropdownOpen = false;
            return;
        }
        this.state.isSearchDropdownOpen = true;
        const results = await this.keepLast.add(this._buildSearchResults(searchValue));
        this.state.searchResults = results;
    }

    _closeSearchDropdown({ clearInput = false } = {}) {
        this.state.isSearchDropdownOpen = false;
        if (clearInput) {
            this.state.searchValue = "";
            if (this.inputRef.el) this.inputRef.el.value = "";
        }
    }

    _onInputSearch() { this._syncSearchResults(); }
    _onInputFocus() { if (this.state.searchValue.trim()) this._syncSearchResults(); }
    _onInputBlur() {
        if (hasTouch()) return;
        setTimeout(() => {
            if (!this.searchContainerRef.el?.contains(document.activeElement)) this._closeSearchDropdown();
        }, 100);
    }

    async _selectSearchResult(result) {
        if (!result) return;
        this._closeSearchDropdown({ clearInput: true });
        if (result.action) {
            await result.action();
        } else if (result.menu) {
            this.menus.selectMenu(result.menu);
        } else if (result.href) {
            window.location.href = result.href;
        }
    }

    _onInputKeydown(ev) {
        ev.stopPropagation();
        if (!this.state.isSearchDropdownOpen) return;
        if (ev.key === "Enter") {
            ev.preventDefault();
            this._selectSearchResult(this.state.searchResults[this.state.searchResultIndex]);
        } else if (ev.key === "Escape") {
            this._closeSearchDropdown({ clearInput: true });
        } else if (ev.key === "ArrowDown") {
            ev.preventDefault();
            this.state.searchResultIndex = (this.state.searchResultIndex + 1) % this.state.searchResults.length;
        } else if (ev.key === "ArrowUp") {
            ev.preventDefault();
            this.state.searchResultIndex = (this.state.searchResultIndex - 1 + this.state.searchResults.length) % this.state.searchResults.length;
        }
    }

    _onWindowKeydown(ev) {
        if (!this.inputRef.el) return;

        const activeEl = document.activeElement;
        const activeTag = activeEl ? activeEl.tagName : "";
        const isContentEditable = activeEl && activeEl.isContentEditable;

        // Only trigger if we aren't already in an input, and no modifiers are pressed
        if (
            activeEl !== this.inputRef.el &&
            this.ui.activeElement === document &&
            !["TEXTAREA", "INPUT", "SELECT"].includes(activeTag) &&
            !isContentEditable
        ) {
            if (ev.key && ev.key.length === 1 && !ev.ctrlKey && !ev.altKey && !ev.metaKey) {
                // Focus the input and manually append the typed character
                ev.preventDefault();
                this.inputRef.el.focus();
                this.inputRef.el.value += ev.key;
                this._syncSearchResults();
            }
        }
    }

    _getAppStyle(app, index) {
        const pos = this.state.homeMenuLayout[app.xmlid] || this._getDefaultAppPosition(index);
        const { cardW, cardH } = this._getLayoutConstants();
        return `left: ${pos.x}px; top: ${pos.y}px; width: ${cardW}px; height: ${cardH}px;`;
    }

    _onAppPointerDown(ev, app) {
        if (ev.button !== 0) return;
        const appIndex = this.displayedApps.findIndex((menu) => menu.xmlid === app.xmlid);
        const pos = this.state.homeMenuLayout[app.xmlid] || this._getDefaultAppPosition(appIndex);
        this.dragState = {
            appXmlid: app.xmlid,
            pointerId: ev.pointerId,
            startX: ev.clientX,
            startY: ev.clientY,
            startLeft: pos.x,
            startTop: pos.y,
            element: ev.currentTarget,
            hasCaptured: false,
            canDrag: this.state.isEditMode,
        };
        window.addEventListener("pointermove", this.boundOnAppPointerMove, { passive: false });
        window.addEventListener("pointerup", this.boundOnAppPointerUp);
        window.addEventListener("pointercancel", this.boundOnAppPointerUp);
    }

    _onAppPointerMove(ev) {
        if (!this.dragState || !this.dragState.canDrag || ev.pointerId !== this.dragState.pointerId) return;
        const deltaX = ev.clientX - this.dragState.startX;
        const deltaY = ev.clientY - this.dragState.startY;
        if (this.state.draggedAppXmlid === null) {
            if (Math.hypot(deltaX, deltaY) < APP_DRAG_THRESHOLD) return;
            this.state.draggedAppXmlid = this.dragState.appXmlid;
            this.dragState.element.setPointerCapture?.(ev.pointerId);
            this.dragState.hasCaptured = true;
            this.dragState.element.style.transition = 'none';
            this.justDraggedUntil = Date.now() + 1000;
        }
        ev.preventDefault();
        const constants = this._getLayoutConstants();
        const maxX = Math.max(constants.padding, this._getCanvasWidth() - constants.cardW - constants.padding);
        this.dragState.lastX = Math.min(Math.max(this.dragState.startLeft + deltaX, constants.padding), maxX);
        this.dragState.lastY = Math.max(this.dragState.startTop + deltaY, constants.padding);

        this.dragState.element.style.left = `${this.dragState.lastX}px`;
        this.dragState.element.style.top = `${this.dragState.lastY}px`;
    }

    async _onAppPointerUp(ev) {
        if (!this.dragState || ev.pointerId !== this.dragState.pointerId) return;
        const draggedXmlid = this.state.draggedAppXmlid;
        if (draggedXmlid) {
            const constants = this._getLayoutConstants();
            const targetGrid = this._pixelsToGrid(this.dragState.lastX, this.dragState.lastY, constants);

            const totalApps = this.displayedApps.length;
            const totalRows = Math.ceil(totalApps / constants.columns);
            let itemsInRow = constants.columns;
            if (targetGrid.row >= totalRows - 1) {
                itemsInRow = totalApps % constants.columns || (totalApps > 0 ? constants.columns : 0);
            }
            const targetPixels = this._gridToPixels(targetGrid.col, targetGrid.row, constants, itemsInRow);

            const occupyingApp = this._getAppAtPosition(targetPixels.x, targetPixels.y, draggedXmlid);
            if (occupyingApp) {
                const [otherXmlid] = occupyingApp;
                const startGrid = this._pixelsToGrid(this.dragState.startLeft, this.dragState.startTop, constants);

                let startItemsInRow = constants.columns;
                if (startGrid.row >= totalRows - 1) {
                    startItemsInRow = totalApps % constants.columns || (totalApps > 0 ? constants.columns : 0);
                }
                const startPixels = this._gridToPixels(startGrid.col, startGrid.row, constants, startItemsInRow);

                this.state.homeMenuLayout[draggedXmlid] = { ...targetGrid, ...targetPixels };
                this.state.homeMenuLayout[otherXmlid] = { ...startGrid, ...startPixels };
            } else {
                this.state.homeMenuLayout[draggedXmlid] = { ...targetGrid, ...targetPixels };
            }
            this.justDraggedUntil = Date.now() + APP_CLICK_SUPPRESSION_DELAY;
            await this._saveHomeMenuLayout();
        } else {
            this.justDraggedUntil = 0;
        }
        if (this.dragState.element) this.dragState.element.style.transition = '';
        this._cleanupDrag();
        this.state.canvasHeight = this._computeCanvasHeight(this.state.homeMenuLayout);
    }

    _cleanupDrag() {
        const dragElement = this.dragState?.element;
        const pointerId = this.dragState?.pointerId;
        if (this.dragState?.hasCaptured && dragElement?.hasPointerCapture?.(pointerId)) dragElement.releasePointerCapture(pointerId);
        window.removeEventListener("pointermove", this.boundOnAppPointerMove);
        window.removeEventListener("pointerup", this.boundOnAppPointerUp);
        window.removeEventListener("pointercancel", this.boundOnAppPointerUp);
        this.dragState = null;
        this.state.draggedAppXmlid = null;
    }
}
CustomHomeMenu.template = "custom_home_app.HomeMenu";
CustomHomeMenu.props = { ...standardActionServiceProps };

registry.category("actions").add("custom_home_app.action_home_menu", CustomHomeMenu);

patch(WebClient.prototype, {
    setup() {
        super.setup(...arguments);
        if (this.env.services.home_menu) {
            // Intercept Enterprise home menu toggle to use our custom dashboard
            this.env.services.home_menu.toggle = async (show) => {
                return this.env.services.action.doAction("custom_home_app.action_home_menu", { clearBreadcrumbs: true });
            };
        }
    },
    async _loadDefaultApp() {
        if (this.env.services.home_menu) {
            return this.env.services.home_menu.toggle();
        }
        return this.env.services.action.doAction("custom_home_app.action_home_menu", { clearBreadcrumbs: true });
    }
});

patch(NavBar.prototype, {
    setup() {
        super.setup(...arguments);
        this.actionService = useService("action");
    },
    onAppsMenuClick(ev) {
        ev.preventDefault();
        this.actionService.doAction("custom_home_app.action_home_menu", { clearBreadcrumbs: true });
    }
});
