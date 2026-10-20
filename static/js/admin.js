// The panel's own behaviour, on every panel page (UNFOLD["SCRIPTS"] in config/settings/base.py).
(() => {
  "use strict";

  const PERSIAN = "۰۱۲۳۴۵۶۷۸۹";
  const toPersian = (text) => String(text).replace(/[0-9]/g, (d) => PERSIAN[Number(d)]);
  const toLatin = (text) =>
    String(text)
      .replace(/[۰-۹]/g, (d) => String(PERSIAN.indexOf(d)))
      .replace(/[٠-٩]/g, (d) => String(d.charCodeAt(0) - 0x0660));

  // apps.core.formatting.format_toman, which test_admin_browser.py holds this to:
  // Persian digits, a Latin comma every three, «تومان», and zero is «رایگان».
  const toman = (value) => {
    const digits = toLatin(value).trim();
    if (!/^[0-9]+$/.test(digits)) return "";
    const whole = BigInt(digits);
    if (whole === 0n) return "رایگان";
    return `${toPersian(whole.toString().replace(/\B(?=([0-9]{3})+$)/g, ","))} تومان`;
  };

  // apps.core.formatting.format_jalali, «۲۶ مهر ۱۴۰۵», held to it the same way.
  // Assembled from parts, so the order does not hang on Intl's own pattern.
  const PERSIAN_CALENDAR = new Intl.DateTimeFormat("fa-IR", {
    calendar: "persian",
    year: "numeric",
    month: "long",
    day: "numeric",
    timeZone: "UTC",
  });
  const jalali = (value) => {
    const found = toLatin(value).trim().match(/^([0-9]{4})[-/]([0-9]{1,2})[-/]([0-9]{1,2})$/);
    if (!found) return "";
    const [year, month, day] = found.slice(1).map(Number);
    const date = new Date(Date.UTC(year, month - 1, day));
    if (date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day) return "";
    const parts = PERSIAN_CALENDAR.formatToParts(date);
    const part = (type) => parts.find((p) => p.type === type).value;
    return `${part("day")} ${part("month")} ${part("year")}`;
  };

  // A split date-time's day, and its time once both boxes hold one, as
  // apps.core.formatting.format_deadline writes the time: «۲۶ مهر ۱۴۰۵، ساعت ۲۳:۳۹».
  const timeBox = (dateBox) => dateBox.closest(".datetime")?.querySelector("input.vTimeField");
  const jalaliDateTime = (dateBox) => {
    const day = jalali(dateBox.value);
    const time = toLatin(timeBox(dateBox)?.value || "")
      .trim()
      .match(/^([01]?[0-9]|2[0-3]):([0-5][0-9])(:[0-5][0-9])?$/);
    if (!day || !time) return day;
    return `${day}، ساعت\u00a0${toPersian(time[1].padStart(2, "0"))}:${toPersian(time[2])}`;
  };

  // Every money column is a `*_price` (ADR-0005). A price typed without
  // separators is one missing zero away from selling at a tenth, so
  // the amount is shown under the field as the shop will print it; the
  // cost price never reaches the shop, so it gets the amount alone. The
  // discount window is typed in the Gregorian calendar the date picker
  // speaks, by an operator who plans in Jalali, so its date gets the same.
  const PREVIEWS = [
    {
      selector: 'input[name$="sale_price"], input[name$="promo_price"]',
      show: (input) => (toman(input.value) ? `در فروشگاه: ${toman(input.value)}` : ""),
      className: "money-preview",
    },
    {
      selector: 'input[name$="cost_price"]',
      show: (input) => toman(input.value),
      className: "money-preview",
    },
    { selector: "input.vDateField", show: jalaliDateTime, className: "date-preview" },
  ];

  const showUnder = (input, { show, className }) => {
    if (input.name.includes("__prefix__")) return;
    let preview = input.parentElement.querySelector(`:scope > .${className}`);
    if (!preview) {
      preview = document.createElement("div");
      preview.className = className;
      input.after(preview);
    }
    preview.textContent = show(input);
    preview.hidden = preview.textContent === "";
  };

  const refresh = (event) => {
    if (!(event.target instanceof HTMLInputElement)) return;
    // A time typed changes the line under its date.
    const target = event.target.matches("input.vTimeField")
      ? event.target.closest(".datetime")?.querySelector("input.vDateField") || event.target
      : event.target;
    for (const kind of PREVIEWS) {
      if (target.matches(kind.selector)) showUnder(target, kind);
    }
  };

  // A page that came back from a failed save holds everything the operator
  // typed and none of it is saved. Unfold's warning arms on the first edit
  // after a page loads, which on this page is none, so leaving lost the work
  // without a word.
  const holdUnsavedWork = () => {
    const form = document.querySelector("form.warn-unsaved-form");
    if (!form || !form.querySelector(".errornote")) return;
    const hold = (event) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", hold);
    form.addEventListener("submit", () => window.removeEventListener("beforeunload", hold));
  };

  // The same failed save renders the saved rows in their stored order, while
  // each row's `sort_order` still holds the operator's drag. Saved as shown,
  // the shop would list them in an order nobody saw, so they are put back in
  // the order they will be saved in. Rows not saved yet stay where they are.
  const showTheOrderThatSaves = () => {
    if (!document.querySelector(".errornote")) return;
    const order = (row) => Number(row.querySelector('input[name$="-sort_order"]')?.value);
    const inlines = new Map();
    document.querySelectorAll(".form-group.original").forEach((row) => {
      if (Number.isNaN(order(row))) return;
      inlines.set(row.parentNode, [...(inlines.get(row.parentNode) || []), row]);
    });
    inlines.forEach((rows, parent) => {
      const after = rows[rows.length - 1].nextSibling;
      [...rows]
        .sort((a, b) => order(a) - order(b))
        .forEach((row) => parent.insertBefore(row, after));
    });
  };

  // The language switch's English side keeps its own digits and names.
  const persianPanel = () => document.documentElement.lang.startsWith("fa");

  // Numbers Django and Unfold write with Latin digits, on load and again on
  // every tick or added row: the selection count, the number on an unsaved
  // inline row, and a search's count of results.
  const LATIN_NUMBERS = ".action-counter, .stacked-counter, h1 .text-subtle";
  const persianNumbers = () => {
    if (!persianPanel()) return;
    document.querySelectorAll(LATIN_NUMBERS).forEach((element) => {
      const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
      for (let node = walker.nextNode(); node; node = walker.nextNode()) {
        const text = toPersian(node.data);
        if (text !== node.data) node.data = text;
      }
    });
  };

  // Material Symbols draw each icon from a word, and a screen reader reads the
  // word: «add», «search», «calendar_today». The icon is hidden from it, and a
  // control that was nothing but an icon is named by its title or in Persian.
  // One with neither keeps its word: a link with no name at all is worse.
  const ICON = ".material-symbols-outlined, .date-icon, .clock-icon";
  const ICON_NAMES = {
    search: ["جستجو", "Search"],
    download: ["دریافت تصویر", "Download the image"],
    storefront: ["پیشخوان", "Dashboard"],
    more_horiz: ["گزینه‌های بیشتر", "More options"],
    "date-icon": ["انتخاب تاریخ", "Choose a date"],
    "clock-icon": ["انتخاب ساعت", "Choose a time"],
  };
  const hasWordsOfItsOwn = (control) => {
    const walker = document.createTreeWalker(control, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      if (node.data.trim() && !node.parentElement.closest(ICON)) return true;
    }
    return false;
  };
  const quietIcons = () => {
    document.querySelectorAll(ICON).forEach((icon) => {
      if (icon.hasAttribute("aria-hidden")) return;
      const control = icon.closest("a, button");
      const named =
        !control ||
        control.title ||
        control.hasAttribute("aria-label") ||
        hasWordsOfItsOwn(control);
      if (!named) {
        const kind = ["date-icon", "clock-icon"].find((name) => icon.classList.contains(name));
        const names = ICON_NAMES[kind || icon.textContent.trim()];
        if (!names) return;
        control.setAttribute("aria-label", names[persianPanel() ? 0 : 1]);
      }
      icon.setAttribute("aria-hidden", "true");
    });
  };

  // `change` too: the date picker's «امروز» sets the value without an input event.
  document.addEventListener("input", refresh);
  document.addEventListener("change", refresh);

  document.addEventListener("DOMContentLoaded", () => {
    for (const kind of PREVIEWS) {
      document.querySelectorAll(kind.selector).forEach((input) => showUnder(input, kind));
    }
    holdUnsavedWork();
    showTheOrderThatSaves();
    const tidy = () => {
      persianNumbers();
      quietIcons();
    };
    // Rows, date pickers and counters keep arriving after load.
    new MutationObserver(tidy).observe(document.body, {
      childList: true,
      characterData: true,
      subtree: true,
    });
    tidy();
  });
})();
