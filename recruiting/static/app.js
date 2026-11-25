(function () {
  "use strict";

  function pad(value) {
    return String(value).padStart(2, "0");
  }

  function addThirtyMinutes(localIso) {
    if (!localIso || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(localIso)) {
      return "";
    }
    var parts = localIso.split("T");
    var d = parts[0].split("-").map(Number);
    var t = parts[1].split(":").map(Number);
    var dt = new Date(d[0], d[1] - 1, d[2], t[0], t[1]);
    if (Number.isNaN(dt.getTime())) {
      return "";
    }
    dt.setMinutes(dt.getMinutes() + 30);
    return (
      dt.getFullYear() +
      "-" +
      pad(dt.getMonth() + 1) +
      "-" +
      pad(dt.getDate()) +
      "T" +
      pad(dt.getHours()) +
      ":" +
      pad(dt.getMinutes())
    );
  }

  document.addEventListener("DOMContentLoaded", function () {
    var startInput = document.querySelector('input[data-slot-start="true"]');
    var endInput = document.querySelector('input[data-slot-end="true"]');
    if (!startInput || !endInput) {
      return;
    }
    startInput.addEventListener("change", function () {
      var computed = addThirtyMinutes(startInput.value);
      if (computed) {
        endInput.value = computed;
      }
    });
  });
})();
