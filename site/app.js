(function () {
  "use strict";

  var DATA_URL = "data/index.json";

  var els = {
    updated: document.getElementById("updated"),
    todayBlock: document.getElementById("today-block"),
    todayTitle: document.getElementById("today-title"),
    today: document.getElementById("today"),
    upcomingBlock: document.getElementById("upcoming-block"),
    upcoming: document.getElementById("upcoming"),
    pastBlock: document.getElementById("past-block"),
    past: document.getElementById("past"),
    pastCount: document.getElementById("past-count"),
    notice: document.getElementById("notice"),
    toast: document.getElementById("toast"),
    tpl: document.getElementById("card-tpl"),
    saveTipTpl: document.getElementById("save-tip-tpl")
  };

  function text(v) {
    return typeof v === "string" ? v.trim() : "";
  }

  function todayStr() {
    var d = new Date();
    var m = String(d.getMonth() + 1).padStart(2, "0");
    var day = String(d.getDate()).padStart(2, "0");
    return d.getFullYear() + "-" + m + "-" + day;
  }

  function isDate(s) {
    return /^\d{4}-\d{2}-\d{2}$/.test(s);
  }

  function formatDuration(sec) {
    var n = Number(sec);
    if (!isFinite(n) || n <= 0) return "";
    n = Math.round(n);
    var h = Math.floor(n / 3600);
    var m = Math.floor((n % 3600) / 60);
    var s = n % 60;
    var mm = String(m).padStart(2, "0");
    var ss = String(s).padStart(2, "0");
    return h > 0 ? h + ":" + mm + ":" + ss : mm + ":" + ss;
  }

  function formatUpdated(s) {
    if (!s) return "";
    var d = new Date(s);
    if (isNaN(d.getTime())) return s;
    var pad = function (n) { return String(n).padStart(2, "0"); };
    return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()) +
      " " + pad(d.getHours()) + ":" + pad(d.getMinutes());
  }

  function formatDateLabel(s) {
    if (!isDate(s)) return "排期未定";
    var t = todayStr();
    var label = s.slice(5).replace("-", " 月 ") + " 日";
    if (s === t) return label + "（今天）";
    if (s < t) return label + "（已过）";
    return label;
  }

  function normalize(raw, i) {
    var ep = raw && typeof raw === "object" ? raw : {};
    var urls = ep.urls && typeof ep.urls === "object" ? ep.urls : {};
    var tags = Array.isArray(ep.tags) ? ep.tags.map(text).filter(Boolean) : [];
    var date = text(ep.scheduled_date);
    return {
      key: text(ep.slug) + "/" + text(ep.id) + "/" + i,
      id: text(ep.id),
      slug: text(ep.slug),
      speaker: text(ep.speaker),
      title: text(ep.title) || "（无标题）",
      desc: text(ep.desc),
      tags: tags,
      date: isDate(date) ? date : "",
      duration: formatDuration(ep.duration_sec),
      status: text(ep.status) || "pending",
      video: text(urls.video),
      cover: text(urls.cover_16x9)
    };
  }

  function sortEpisodes(list) {
    return list.slice().sort(function (a, b) {
      if (a.date !== b.date) {
        if (!a.date) return 1;
        if (!b.date) return -1;
        return a.date < b.date ? -1 : 1;
      }
      return a.key < b.key ? -1 : a.key > b.key ? 1 : 0;
    });
  }

  var toastTimer = null;
  function toast(msg) {
    els.toast.textContent = msg;
    els.toast.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () {
      els.toast.classList.remove("show");
    }, 1600);
  }

  // iOS Safari 上 navigator.clipboard 在非安全上下文或权限被拒时会失败，
  // 回退到选中隐藏文本域再执行复制命令。
  function legacyCopy(value) {
    var ta = document.createElement("textarea");
    ta.value = value;
    ta.setAttribute("readonly", "");
    ta.contentEditable = "true";
    ta.style.position = "fixed";
    ta.style.top = "0";
    ta.style.left = "0";
    ta.style.width = "1px";
    ta.style.height = "1px";
    ta.style.padding = "0";
    ta.style.border = "none";
    ta.style.opacity = "0";
    document.body.appendChild(ta);

    var ok = false;
    try {
      var range = document.createRange();
      range.selectNodeContents(ta);
      var sel = window.getSelection();
      if (sel) {
        sel.removeAllRanges();
        sel.addRange(range);
      }
      ta.setSelectionRange(0, value.length);
      ta.focus();
      ok = document.execCommand("copy");
    } catch (e) {
      ok = false;
    }
    document.body.removeChild(ta);
    return ok;
  }

  function copyText(value, label, btn) {
    function done(ok) {
      if (ok) {
        toast("已复制" + label);
        btn.classList.add("copied");
        setTimeout(function () {
          btn.classList.remove("copied");
        }, 1200);
      } else {
        toast("复制失败，请长按选中文本");
      }
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(value).then(
        function () { done(true); },
        function () { done(legacyCopy(value)); }
      );
    } else {
      done(legacyCopy(value));
    }
  }

  function buildCard(ep, hero) {
    var node = els.tpl.content.firstElementChild.cloneNode(true);
    if (hero) {
      node.classList.add("hero");
      node.querySelector(".body").appendChild(els.saveTipTpl.content.cloneNode(true));
    }

    var img = node.querySelector("img");
    var fallback = node.querySelector(".thumb-fallback");
    if (ep.cover) {
      img.src = ep.cover;
      img.alt = ep.title + " 封面";
      fallback.hidden = true;
      img.addEventListener("error", function () {
        img.hidden = true;
        fallback.hidden = false;
      });
    } else {
      img.hidden = true;
    }

    var badge = node.querySelector(".duration");
    if (ep.duration) badge.textContent = ep.duration;
    else badge.hidden = true;

    node.querySelector(".date").textContent = formatDateLabel(ep.date);
    node.querySelector(".title").textContent = ep.title;

    var metaParts = [];
    if (ep.speaker) metaParts.push(ep.speaker);
    if (ep.slug) metaParts.push(ep.slug + (ep.id ? " / " + ep.id : ""));
    else if (ep.id) metaParts.push(ep.id);
    if (ep.status === "published") metaParts.push("已发布");
    node.querySelector(".meta").textContent = metaParts.join(" · ");

    var dl = node.querySelector(".btn-download");
    if (ep.video) {
      dl.href = ep.video;
    } else {
      dl.removeAttribute("href");
      dl.classList.add("is-disabled");
      dl.setAttribute("aria-disabled", "true");
      dl.textContent = "暂无视频直链";
    }

    var payload = {
      title: { value: ep.title, label: "标题" },
      tags: { value: ep.tags.join(" "), label: "标签" },
      desc: { value: ep.desc, label: "简介" }
    };

    Array.prototype.forEach.call(node.querySelectorAll("[data-copy]"), function (btn) {
      var item = payload[btn.getAttribute("data-copy")];
      if (!item || !item.value) {
        btn.disabled = true;
        return;
      }
      btn.addEventListener("click", function () {
        copyText(item.value, item.label, btn);
      });
    });

    return node;
  }

  function renderList(container, list, hero) {
    var frag = document.createDocumentFragment();
    list.forEach(function (ep) {
      frag.appendChild(buildCard(ep, hero));
    });
    container.textContent = "";
    container.appendChild(frag);
  }

  function pickHero(sorted, today) {
    var pending = sorted.filter(function (ep) {
      return ep.status !== "published";
    });
    var onToday = pending.filter(function (ep) {
      return ep.date === today;
    })[0];
    if (onToday) return { ep: onToday, title: "今天该发这条" };

    var future = pending.filter(function (ep) {
      return ep.date && ep.date > today;
    })[0];
    if (future) return { ep: future, title: "最近一条待发" };

    var overdue = pending.filter(function (ep) {
      return ep.date && ep.date < today;
    });
    if (overdue.length) {
      return { ep: overdue[overdue.length - 1], title: "最近一条待发（已过排期）" };
    }

    var undated = pending[0];
    if (undated) return { ep: undated, title: "最近一条待发" };
    return null;
  }

  function showNotice(msg) {
    els.notice.textContent = msg;
    els.notice.hidden = false;
  }

  function render(data) {
    var raw = data && Array.isArray(data.episodes) ? data.episodes : [];
    var episodes = sortEpisodes(raw.map(normalize));

    var updated = formatUpdated(text(data && data.updated_at));
    els.updated.textContent = updated ? "数据更新于 " + updated : "";

    if (!episodes.length) {
      showNotice("暂无可发布的集数。等 CI 生成并写入 data/index.json 后，这里会自动出现。");
      return;
    }

    var today = todayStr();
    var hero = pickHero(episodes, today);

    if (hero) {
      els.todayTitle.textContent = hero.title;
      renderList(els.today, [hero.ep], true);
      els.todayBlock.hidden = false;
    }

    var rest = episodes.filter(function (ep) {
      return !hero || ep !== hero.ep;
    });
    var past = rest.filter(function (ep) {
      return ep.date && ep.date < today;
    });
    var upcoming = rest.filter(function (ep) {
      return past.indexOf(ep) === -1;
    });

    if (upcoming.length) {
      renderList(els.upcoming, upcoming, false);
      els.upcomingBlock.hidden = false;
    }
    if (past.length) {
      renderList(els.past, past, false);
      els.pastCount.textContent = past.length + " 条";
      els.pastBlock.hidden = false;
    }
  }

  fetch(DATA_URL, { cache: "no-store" })
    .then(function (res) {
      if (!res.ok) throw new Error("HTTP " + res.status);
      return res.json();
    })
    .then(render)
    .catch(function (err) {
      showNotice("数据加载失败：" + (err && err.message ? err.message : "未知错误") +
        "。请确认 data/index.json 存在且为合法 JSON。");
    });
})();
