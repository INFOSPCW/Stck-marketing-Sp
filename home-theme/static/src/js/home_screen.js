/** @odoo-module **/

import { Component, useState, onWillStart, onMounted } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";
import { rpc } from "@web/core/network/rpc";

export class HomeScreenDashboard extends Component {
    static template = "HomeTheme.Dashboard";
    static props = { ...standardActionServiceProps };

    setup() {
        // Services required for this component
        this.action = useService("action");
        this.menu = useService("menu");

        this.state = useState({
            apps: [],
            userName: '',
            companyName: '',
            backgroundSettings: null,
        });

        this.isDragging = false;

        onWillStart(async () => {
            await this.loadHomeScreenData();
        });

        onMounted(() => {
            // Apply background settings after DOM is ready
            if (this.state.backgroundSettings) {
                this.applyBackgroundSettings(this.state.backgroundSettings);
            }

            // Initialize drag and drop with a slight delay to ensure DOM is fully ready
            setTimeout(() => {
                this.initDragAndDrop();
            }, 100);
        });
    }

    async loadHomeScreenData() {
        try {
            const data = await rpc("/web/home_screen", {});
            console.log('Home screen data received:', data);
            this.state.apps = data.apps || [];
            this.state.userName = data.user_name || '';
            this.state.companyName = data.company_name || '';

            // Store background settings to apply after mount
            this.state.backgroundSettings = {
                background_type: data.background_type,
                background_image: data.background_image,
                background_color: data.background_color,
            };

            console.log('Loaded apps count:', this.state.apps.length);
        } catch (error) {
            console.error('Error loading home screen data:', error);
            this.state.apps = [];
        }
    }

    applyBackgroundSettings(data) {
        const dashboard = this.el || document.querySelector('.o_home_screen_dashboard');

        console.log('Applying background settings:', data);
        console.log('Dashboard element:', dashboard);

        if (!dashboard) {
            console.error('Dashboard element not found!');
            return;
        }

        // Apply background based on type
        if (data.background_type === 'image' && data.background_image) {
            console.log('Applying custom background image');
            // Apply custom background image
            dashboard.style.backgroundImage = `url('data:image/png;base64,${data.background_image}')`;
            dashboard.style.backgroundSize = 'cover';
            dashboard.style.backgroundPosition = 'center';
            dashboard.style.backgroundRepeat = 'no-repeat';
        } else if (data.background_type === 'gradient') {
            console.log('Applying gradient background');
            // Apply gradient background
            dashboard.style.background = 'linear-gradient(135deg, #f5f7fa 0%, #e8ecf1 100%)';
            dashboard.style.backgroundImage = 'none';
        } else if (data.background_type === 'solid') {
            console.log('Applying solid color background');
            // Apply solid color background
            dashboard.style.background = data.background_color || '#f5f7fa';
            dashboard.style.backgroundImage = 'none';
        }
    }

    initDragAndDrop() {
        console.log('Initializing drag and drop, el:', this.el);

        // Use document.querySelector as fallback if this.el is not available
        const container = this.el || document.querySelector('.o_home_screen_dashboard');

        if (!container) {
            console.error('Container not found');
            return;
        }

        const appCards = container.querySelectorAll('.o_home_app_card');
        const grid = container.querySelector('.o_home_apps_grid');

        console.log('Found app cards:', appCards.length);
        console.log('Found grid:', !!grid);

        if (!appCards || appCards.length === 0 || !grid) {
            console.log('Drag and drop not initialized - missing elements');
            console.log('App cards:', appCards?.length);
            console.log('Grid:', !!grid);
            return;
        }

        let draggedElement = null;

        appCards.forEach((card) => {
            // Make cards draggable
            card.setAttribute('draggable', 'true');

            card.addEventListener('dragstart', (e) => {
                this.isDragging = true;
                draggedElement = card;
                card.classList.add('o_dragging');
                e.dataTransfer.effectAllowed = 'move';
                e.dataTransfer.setData('text/html', '');
                console.log('Drag started');
            });

            card.addEventListener('dragend', (e) => {
                card.classList.remove('o_dragging');
                setTimeout(() => {
                    this.isDragging = false;
                    this.saveAppOrder();
                }, 100);
                console.log('Drag ended');
            });
        });

        // Handle dragover on the grid container
        grid.addEventListener('dragover', (e) => {
            e.preventDefault();
            e.dataTransfer.dropEffect = 'move';

            const afterElement = this.getDragAfterElement(grid, e.clientX, e.clientY);

            if (draggedElement) {
                if (afterElement == null) {
                    grid.appendChild(draggedElement);
                } else {
                    grid.insertBefore(draggedElement, afterElement);
                }
            }
        });

        grid.addEventListener('drop', (e) => {
            e.preventDefault();
        });
    }

    getDragAfterElement(container, x, y) {
        const draggableElements = [...container.querySelectorAll('.o_home_app_card:not(.o_dragging)')];

        return draggableElements.reduce((closest, child) => {
            const box = child.getBoundingClientRect();
            const offsetX = x - box.left - box.width / 2;
            const offsetY = y - box.top - box.height / 2;
            const offset = Math.sqrt(offsetX * offsetX + offsetY * offsetY);

            if (offset < closest.offset) {
                return { offset: offset, element: child };
            } else {
                return closest;
            }
        }, { offset: Number.POSITIVE_INFINITY }).element;
    }

    async saveAppOrder() {
        try {
            // Use same container logic as initDragAndDrop
            const container = this.el || document.querySelector('.o_home_screen_dashboard');
            if (!container) {
                console.error('Container not found for saving order');
                return;
            }

            const appCards = container.querySelectorAll('.o_home_app_card');
            if (!appCards || appCards.length === 0) {
                console.error('No app cards found for saving order');
                return;
            }

            // Get the current order of app IDs
            const appIds = [];
            appCards.forEach((card) => {
                const appId = parseInt(card.dataset.appId);
                if (appId) {
                    appIds.push(appId);
                }
            });

            console.log('Saving app order:', appIds);

            // Save to backend
            const result = await rpc('/web/home_screen/save_order', { app_ids: appIds });

            if (result.success) {
                console.log('App order saved successfully');
                // Update state to reflect new order
                const newAppsOrder = appIds.map(id => this.state.apps.find(app => app.id === id)).filter(Boolean);
                this.state.apps = newAppsOrder;
            } else {
                console.error('Failed to save app order:', result.error);
            }
        } catch (error) {
            console.error('Error saving app order:', error);
        }
    }

    async onAppClick(app, ev) {
        // Prevent click if we were just dragging
        if (this.isDragging) {
            ev.preventDefault();
            ev.stopPropagation();
            return;
        }

        if (!app || !app.id) {
            console.error('Invalid app data:', app);
            return;
        }

        try {
            // Use the menu service to select the menu by ID
            // This is the correct way to open an app in Odoo
            await this.menu.selectMenu(app.id);
        } catch (error) {
            console.error('Error opening app:', error);
        }
    }
}

// Register the component as a client action
registry.category("actions").add("home_screen_dashboard", HomeScreenDashboard);
