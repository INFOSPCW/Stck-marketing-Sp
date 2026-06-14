import { registry } from "@web/core/registry";

const SEARCH_APP_XMLID = "custom_home_app.test_home_menu_mail_app";
const DRAG_FROM_XMLID = "custom_home_app.test_home_menu_drag_alpha";

function getDraggableApp(xmlid) {
    return document
        .querySelector(`.o_home_menu .o_app[data-menu-xmlid="${xmlid}"]`)
        ?.closest(".o_draggable");
}

registry.category("web_tour.tours").add("home_desk_app_home_menu_tour", {
    steps: () => [
        {
            trigger: `.o_home_menu .o_app[data-menu-xmlid="${SEARCH_APP_XMLID}"]`,
        },
        {
            trigger: ".o_home_menu .o_custom_search",
            run: "edit Mail",
        },
        {
            trigger: '.o_home_menu_search_dropdown .o_home_menu_search_result:has(.o_home_menu_search_result_label:contains("Mailbox"))',
            run() {
                if (document.querySelector(".o_command_palette")) {
                    throw new Error(
                        "Default command palette opened instead of the inline home menu search dropdown."
                    );
                }
            },
        },
        {
            trigger: '.o_home_menu_search_dropdown .o_home_menu_search_result:has(.o_home_menu_search_result_label:contains("Mailbox"))',
            run: "click",
        },
        {
            trigger: ".o_main_navbar .o_menu_toggle",
            run() {
                if (document.querySelector(".o_home_menu")) {
                    throw new Error("Selecting the inline search result did not open the app.");
                }
            },
        },
        {
            trigger: ".o_navbar_apps_menu",
            run: "click",
        },
        {
            trigger: `.o_home_menu .o_app[data-menu-xmlid="${DRAG_FROM_XMLID}"]`,
            run() {
                const draggable = getDraggableApp(DRAG_FROM_XMLID);
                if (!draggable) {
                    throw new Error("Drag source app is missing from the home menu.");
                }
                window.homeMenuPositionBefore = {
                    left: parseFloat(draggable.style.left || "0"),
                    top: parseFloat(draggable.style.top || "0"),
                };
            },
        },
        {
            trigger: `.o_home_menu .o_app[data-menu-xmlid="${DRAG_FROM_XMLID}"]`,
            async run() {
                const draggable = getDraggableApp(DRAG_FROM_XMLID);
                const canvas = document.querySelector(".o_home_menu .o_apps_canvas");
                const before = window.homeMenuPositionBefore;
                if (!draggable || !canvas || !before) {
                    throw new Error("Missing drag canvas or initial app position.");
                }
                const draggableRect = draggable.getBoundingClientRect();
                const canvasRect = canvas.getBoundingClientRect();
                const targetLeft = Math.max(
                    80,
                    Math.min(canvas.clientWidth - 100, before.left + 220)
                );
                const targetTop = Math.max(80, before.top + 140);
                const startX = draggableRect.left + draggableRect.width / 2;
                const startY = draggableRect.top + draggableRect.height / 2;
                const targetX = canvasRect.left + targetLeft + draggableRect.width / 2;
                const targetY = canvasRect.top + targetTop + draggableRect.height / 2;

                const triggerPointerEvent = (type, target, x, y) => {
                    target.dispatchEvent(
                        new PointerEvent(type, {
                            bubbles: true,
                            button: 0,
                            buttons: type === "pointerup" ? 0 : 1,
                            pointerId: 1,
                            pointerType: "mouse",
                            pageX: x,
                            pageY: y,
                            clientX: x,
                            clientY: y,
                        })
                    );
                };

                triggerPointerEvent("pointerdown", draggable, startX, startY);
                await new Promise((resolve) => setTimeout(resolve, 0));
                triggerPointerEvent("pointermove", window, startX + 12, startY + 12);
                triggerPointerEvent("pointermove", window, targetX, targetY);
                triggerPointerEvent("pointerup", window, targetX, targetY);
                await new Promise((resolve) => setTimeout(resolve, 0));
            },
        },
        {
            trigger: `.o_home_menu .o_app[data-menu-xmlid="${DRAG_FROM_XMLID}"]`,
            run() {
                const draggable = getDraggableApp(DRAG_FROM_XMLID);
                const before = window.homeMenuPositionBefore;
                if (!draggable || !before) {
                    throw new Error("Drag verification state is missing.");
                }
                const after = {
                    left: parseFloat(draggable.style.left || "0"),
                    top: parseFloat(draggable.style.top || "0"),
                };
                if (after.left <= before.left && after.top <= before.top) {
                    throw new Error("Free drag did not move the app card on the home menu.");
                }
            },
        },
    ],
});
