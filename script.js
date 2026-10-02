/* ==========================================================================
   CampusHub - front-end behaviour
   --------------------------------------------------------------------------
   Plain JavaScript (no frameworks, no build step). Responsibilities:
     1. Small helpers (DOM, API calls, alerts, validation messages)
     2. Shared UI: mobile navigation, password show/hide, footer year
     3. Header auth area (shows "Dashboard" + "Logout" when logged in)
     4. register.html  -> POST /api/auth/register
     5. login.html     -> POST /api/auth/login  (role-based redirect)
     6. Dashboards     -> GET /api/auth/me, role guard, panel switching, logout
   All session handling relies on an httpOnly cookie, so the browser never has
   to store a token in localStorage and the user's role always comes from the
   server, never from the page.
   ========================================================================== */
(function () {
  'use strict';

  /* ------------------------------------------------------------------ *
   * 1. Helpers
   * ------------------------------------------------------------------ */
  var $ = function (selector, root) { return (root || document).querySelector(selector); };
  var $$ = function (selector, root) {
    return Array.prototype.slice.call((root || document).querySelectorAll(selector));
  };

  var ROUTES = {
    home: '/index.html',
    login: '/login.html',
    register: '/register.html',
    dashboards: {
      student: '/student-dashboard.html',
      faculty: '/faculty-dashboard.html',
      admin: '/admin-dashboard.html'
    }
  };

  var ROLE_LABELS = { student: 'Student', faculty: 'Faculty', admin: 'Admin' };
  var currentUser = null;

  function roleLabel(role) {
    return ROLE_LABELS[role] || role || '';
  }

  function dashboardFor(role) {
    return ROUTES.dashboards[role] || null;
  }

  /** Calls the JSON API. Resolves with { ok, status, data } and never throws
   *  for HTTP errors - only for network problems. */
  function api(path, options) {
    options = options || {};
    var method = (options.method || 'GET').toUpperCase();
    var init = {
      method: method,
      headers: { Accept: 'application/json' },
      credentials: 'same-origin'
    };

    if (['POST', 'PUT', 'PATCH', 'DELETE'].indexOf(method) !== -1) {
      var csrfCookie = document.cookie.match(/(?:^|;\s*)campushub_csrf=([^;]+)/);
      if (csrfCookie) {
        init.headers['X-CSRF-Token'] = decodeURIComponent(csrfCookie[1]);
      }
    }

    if (options.body !== undefined && options.body !== null) {
      if (typeof FormData !== 'undefined' && options.body instanceof FormData) {
        init.body = options.body;
      } else {
        init.headers['Content-Type'] = 'application/json';
        init.body = JSON.stringify(options.body);
      }
    }

    return fetch(path, init).then(function (response) {
      return response
        .json()
        .catch(function () { return {}; })
        .then(function (data) {
          return { ok: response.ok, status: response.status, data: data || {} };
        });
    }).catch(function () {
      return {
        ok: false,
        status: 0,
        data: {
          message: 'Cannot reach the CampusHub server. Make sure it is running ' +
                   '(.\\.venv\\Scripts\\python.exe -m flask --app flask_backend.app run) ' +
                   'and open the site at http://localhost:5000.'
        }
      };
    });
  }

  function showAlert(element, type, message) {
    if (!element) { return; }
    element.className = 'alert alert-' + type;
    element.textContent = message;
    element.hidden = false;
  }

  function hideAlert(element) {
    if (!element) { return; }
    element.hidden = true;
    element.textContent = '';
  }

  function fieldWrap(input) {
    if (!input) { return null; }
    return input.closest('.field') || input.parentElement;
  }

  function setFieldError(input, message) {
    if (!input) { return; }
    var wrap = fieldWrap(input);
    if (wrap) { wrap.classList.add('has-error'); }
    var errorId = input.id ? input.id + 'Error' : null;
    var errorNode = errorId ? document.getElementById(errorId) : null;
    if (!errorNode) {
      errorNode = $('.field-error', wrap || document);
    }
    if (errorNode) {
      errorNode.textContent = message;
      errorNode.hidden = false;
    }
    input.setAttribute('aria-invalid', 'true');
  }

  function clearFieldErrors(scope) {
    $$('.field.has-error', scope || document).forEach(function (wrap) {
      wrap.classList.remove('has-error');
    });
    $$('.field-error', scope || document).forEach(function (node) {
      node.hidden = true;
      node.textContent = '';
    });
    $$('[aria-invalid]', scope || document).forEach(function (input) {
      input.removeAttribute('aria-invalid');
    });
  }

  var EMAIL_RE = /^[^\s@]+@[^\s@]+\.[A-Za-z]{2,}$/;

  function setBusy(button, busy, busyLabel) {
    if (!button) { return; }
    if (busy) {
      button.dataset.originalLabel = button.textContent;
      button.disabled = true;
      button.textContent = busyLabel || 'Please wait…';
    } else {
      button.disabled = false;
      if (button.dataset.originalLabel) {
        button.textContent = button.dataset.originalLabel;
      }
    }
  }

  function formatDate(value) {
    if (!value) { return '—'; }
    var date = new Date(value);
    if (isNaN(date.getTime())) { return '—'; }
    return date.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
  }

  function queryParam(name) {
    return new URLSearchParams(window.location.search).get(name);
  }

  function initBranchOptions() {
    api('/api/branches').then(function (result) {
      if (!result.ok) { return; }
      var branches = result.data.branches || [];
      [
        '#branch', '#loginBranch', '#resourceBranch', '#resourceBranchFilter',
        '#studentResourceBranch', '#assignmentBranch'
      ].forEach(function (selector) {
        var select = $(selector);
        if (!select) { return; }
        var selected = select.value;
        var first = select.options.length ? select.options[0].cloneNode(true) : null;
        select.innerHTML = '';
        if (first) { select.appendChild(first); }
        branches.forEach(function (branchName) {
          var option = document.createElement('option');
          option.value = branchName;
          option.textContent = branchName;
          select.appendChild(option);
        });
        if (branches.indexOf(selected) !== -1) { select.value = selected; }
      });
    });
  }

  function initCategoryOptions() {
    api('/api/categories').then(function (result) {
      if (!result.ok) { return; }
      $$('#resourceCategoryFilter, .resource-category-select').forEach(function (select) {
        var known = Array.prototype.map.call(select.options, function (option) {
          return option.value || option.textContent;
        });
        (result.data.categories || []).forEach(function (category) {
          if (known.indexOf(category) !== -1) { return; }
          var option = document.createElement('option');
          option.value = category;
          option.textContent = category;
          select.appendChild(option);
          known.push(category);
        });
      });
    });
  }

  /* ------------------------------------------------------------------ *
   * 2. Shared UI behaviour
   * ------------------------------------------------------------------ */
  function initNavToggles() {
    var toggle = $('#navToggle');
    if (!toggle) { return; }

    // The toggle targets either the public nav (#primaryNav) or the
    // dashboard sidebar (#dashSidebar), declared through aria-controls.
    var targetId = toggle.getAttribute('aria-controls');
    var target = targetId ? document.getElementById(targetId) : null;
    if (!target) { return; }

    toggle.addEventListener('click', function () {
      var isOpen = toggle.getAttribute('aria-expanded') === 'true';
      toggle.setAttribute('aria-expanded', String(!isOpen));
      target.classList.toggle('is-open', !isOpen);
    });

    // Close the menu after choosing a destination.
    $$('a', target).forEach(function (link) {
      link.addEventListener('click', function () {
        toggle.setAttribute('aria-expanded', 'false');
        target.classList.remove('is-open');
      });
    });

    // Close on Escape or on a click outside the menu.
    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') {
        toggle.setAttribute('aria-expanded', 'false');
        target.classList.remove('is-open');
      }
    });
    document.addEventListener('click', function (event) {
      if (!target.classList.contains('is-open')) { return; }
      if (target.contains(event.target) || toggle.contains(event.target)) { return; }
      toggle.setAttribute('aria-expanded', 'false');
      target.classList.remove('is-open');
    });
  }

  function initPasswordToggles() {
    $$('[data-toggle-password]').forEach(function (button) {
      var input = document.getElementById(button.getAttribute('data-toggle-password'));
      if (!input) { return; }
      button.addEventListener('click', function () {
        var show = input.type === 'password';
        input.type = show ? 'text' : 'password';
        button.textContent = show ? 'Hide' : 'Show';
        button.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
        input.focus({ preventScroll: true });
      });
    });
  }

  function initFooterYear() {
    var year = String(new Date().getFullYear());
    $$('[data-year]').forEach(function (node) { node.textContent = year; });
  }

  /** Fills every [data-auth-slot] on public pages with either
   *  Login/Register (logged out) or Dashboard/Logout (logged in). */
  function renderHeaderAuth(session) {
    $$('[data-auth-slot]').forEach(function (slot) {
      slot.innerHTML = '';

      if (!session) {
        var login = document.createElement('a');
        login.className = 'btn btn-outline btn-sm';
        login.href = ROUTES.login;
        login.textContent = 'Login';

        var register = document.createElement('a');
        register.className = 'btn btn-primary btn-sm';
        register.href = ROUTES.register;
        register.textContent = 'Register';

        slot.appendChild(login);
        slot.appendChild(register);
        return;
      }

      var dashboard = dashboardFor(session.role);
      if (dashboard) {
        var dash = document.createElement('a');
        dash.className = 'btn btn-primary btn-sm';
        dash.href = dashboard;
        dash.textContent = 'Go to my dashboard';
        slot.appendChild(dash);
      }

      var logout = document.createElement('button');
      logout.type = 'button';
      logout.className = 'btn btn-outline btn-sm';
      logout.textContent = 'Logout';
      logout.addEventListener('click', function () { logoutAndRedirect(logout); });
      slot.appendChild(logout);
    });
  }

  /** Ends the session on the server, then returns to the login page. */
  function logoutAndRedirect(button) {
    setBusy(button, true, 'Logging out…');
    return api('/api/auth/logout', { method: 'POST' }).then(function (result) {
      setBusy(button, false);
      window.location.href = ROUTES.login + '?loggedout=1';
      return result;
    });
  }

  function initLogoutButtons() {
    $$('[data-logout]').forEach(function (button) {
      button.addEventListener('click', function () { logoutAndRedirect(button); });
    });
  }

  /* ------------------------------------------------------------------ *
   * 3. Registration page
   * ------------------------------------------------------------------ */
  function initRegisterPage(form) {
    var alertBox = $('#formAlert');
    var submit = $('#registerSubmit');
    var fullName = $('#fullName');
    var email = $('#email');
    var password = $('#password');
    var confirmPassword = $('#confirmPassword');
    var branch = $('#branch');
    var rollNumber = $('#rollNumber');

    // Show the message the server sent when it bounced us back here.
    if (queryParam('registered') === '1') {
      showAlert(alertBox, 'success', 'Your account was created. Please log in to continue.');
    }

    form.addEventListener('submit', function (event) {
      event.preventDefault();
      hideAlert(alertBox);
      clearFieldErrors(form);

      var values = {
        fullName: fullName.value.trim(),
        email: email.value.trim().toLowerCase(),
        password: password.value,
        confirmPassword: confirmPassword.value
      };
      values.branch = branch ? branch.value : '';
      values.rollNumber = rollNumber ? rollNumber.value.trim().toUpperCase() : '';
      var roleInput = form.querySelector('input[name="role"]:checked');
      values.role = roleInput ? roleInput.value : '';

      /* ----- Client-side validation (the server validates again) ----- */
      var hasError = false;

      if (!values.fullName) {
        setFieldError(fullName, 'Full name is required.');
        hasError = true;
      } else if (values.fullName.length < 3) {
        setFieldError(fullName, 'Please enter your full name (at least 3 characters).');
        hasError = true;
      }

      if (!values.email) {
        setFieldError(email, 'Email is required.');
        hasError = true;
      } else if (!EMAIL_RE.test(values.email)) {
        setFieldError(email, 'Enter a valid email address, for example you@college.edu.');
        hasError = true;
      }

      if (!values.password) {
        setFieldError(password, 'Password is required.');
        hasError = true;
      } else if (values.password.length < 6) {
        setFieldError(password, 'Password must be at least 6 characters long.');
        hasError = true;
      }

      if (!values.confirmPassword) {
        setFieldError(confirmPassword, 'Please confirm your password.');
        hasError = true;
      } else if (values.password !== values.confirmPassword) {
        setFieldError(confirmPassword, 'The two passwords do not match.');
        hasError = true;
      }

      if (!values.role) {
        var roleError = document.getElementById('roleError');
        if (roleError) {
          roleError.textContent = 'Please select a role.';
          roleError.hidden = false;
        }
        if (values.rollNumber && !/^[A-Z0-9][A-Z0-9/-]{1,31}$/.test(values.rollNumber)) {
          setFieldError(rollNumber, 'Use 2–32 letters, numbers, / or - for the roll number.');
          hasError = true;
        }
        hasError = true;
      }

      if (hasError) {
        showAlert(alertBox, 'error', 'Please fix the highlighted fields and try again.');
        return;
      }

      /* ----- Send the registration to the server ----- */
      setBusy(submit, true, 'Creating your account…');

      api('/api/auth/register', { method: 'POST', body: values }).then(function (result) {
        setBusy(submit, false);

        if (result.ok) {
          showAlert(alertBox, 'success',
            result.data.message || 'Account created successfully. Redirecting to login…');
          form.reset();
          window.setTimeout(function () {
            window.location.href = ROUTES.login + '?registered=1&email=' +
              encodeURIComponent(values.email);
          }, 1100);
          return;
        }

        // Field-level errors from the API, e.g. { email: 'Email is already registered.' }
        var errors = result.data.errors || {};
        Object.keys(errors).forEach(function (key) {
          var input = form.elements[key];
          if (input && input.length === undefined) {   // plain input
            setFieldError(input, errors[key]);
          }
        });

        showAlert(alertBox, 'error',
          result.data.message || 'Registration failed. Please try again.');

        if (/already|exists|duplicate/i.test(result.data.message || '')) {
          setFieldError(email, result.data.message);
        }
        if (/password/i.test(result.data.message || '')) {
          setFieldError(password, result.data.message);
        }
      });
    });
  }

  /* ------------------------------------------------------------------ *
   * 4. Login page
   * ------------------------------------------------------------------ */
  function initLoginPage(form) {
    var alertBox = $('#formAlert');
    var submit = $('#loginSubmit');
    var email = $('#email');
    var password = $('#password');
    var expectedRole = $('#expectedRole');
    var branch = $('#loginBranch');

    // Messages coming from a redirect.
    if (queryParam('registered') === '1') {
      showAlert(alertBox, 'success', 'Your account was created. Log in with your email and password.');
    } else if (queryParam('loggedout') === '1') {
      showAlert(alertBox, 'info', 'You have been logged out.');
    } else if (queryParam('error') === 'auth_required') {
      showAlert(alertBox, 'warn', 'Please log in to open that page.');
    } else if (queryParam('error') === 'role_not_allowed') {
      showAlert(alertBox, 'warn',
        'That dashboard belongs to a different role. Log in with the correct account.');
    }

    var prefill = queryParam('email');
    if (prefill) { email.value = prefill; }

    form.addEventListener('submit', function (event) {
      event.preventDefault();
      hideAlert(alertBox);
      clearFieldErrors(form);

      var values = {
        email: email.value.trim().toLowerCase(),
        password: password.value,
        branch: branch ? branch.value : ''
      };
      var wanted = expectedRole ? expectedRole.value : '';

      var hasError = false;
      if (!values.email) {
        setFieldError(email, 'Email is required.');
        hasError = true;
      } else if (!EMAIL_RE.test(values.email)) {
        setFieldError(email, 'Enter a valid email address.');
        hasError = true;
      }
      if (!values.password) {
        setFieldError(password, 'Password is required.');
        hasError = true;
      }
      if (hasError) {
        showAlert(alertBox, 'error', 'Please enter your email and password.');
        return;
      }

      setBusy(submit, true, 'Logging in…');

      api('/api/auth/login', { method: 'POST', body: values }).then(function (result) {
        setBusy(submit, false);

        if (!result.ok) {
          showAlert(alertBox, 'error',
            result.data.message || 'Login failed. Check your email and password.');
          if (result.data.code === 'EMAIL_NOT_REGISTERED') {
            setFieldError(email, 'This email is not registered. Please register first.');
          } else if (result.data.code === 'BRANCH_MISMATCH') {
            setFieldError(branch, result.data.message);
          } else if (result.status === 401) {
            setFieldError(password, result.data.message || 'Incorrect password.');
          }
          return;
        }

        var user = result.data.user || {};
        var destination = dashboardFor(user.role);

        if (!destination) {
          showAlert(alertBox, 'error', 'Your account has an unknown role. Contact the administrator.');
          return;
        }

        // If the user picked a role in the form and it does not match the role
        // stored in the database, explain it - then still go where the account
        // belongs. The browser can never choose the role.
        if (wanted && wanted !== user.role) {
          showAlert(alertBox, 'info',
            'Logged in successfully. Opening your ' + roleLabel(user.role).toLowerCase() +
            ' dashboard.');
        } else {
          showAlert(alertBox, 'success', 'Logged in successfully. Opening your dashboard…');
        }

        // Honour ?next= only when it points at the dashboard for this role.
        var requested = queryParam('next');
        var target = requested && requested === destination ? requested : destination;

        window.setTimeout(function () { window.location.href = target; }, wanted && wanted !== user.role ? 1400 : 700);
      });
    });
  }

  /* ------------------------------------------------------------------ *
   * 5. Dashboards (student / faculty / admin)
   * ------------------------------------------------------------------ */
  function renderUserDetails(user) {
    $$('[data-user-name]').forEach(function (node) { node.textContent = user.fullName || '—'; });
    $$('[data-user-email]').forEach(function (node) { node.textContent = user.email || '—'; });
    $$('[data-user-role-label]').forEach(function (node) { node.textContent = roleLabel(user.role); });
    $$('[data-user-created]').forEach(function (node) { node.textContent = formatDate(user.createdAt); });
    $$('[data-user-role]').forEach(function (node) {
      var icons = { student: '\u{1F393}', faculty: '\u{1F4DA}', admin: '\u{1F512}' };
      node.textContent = (icons[user.role] || '') + ' ' + roleLabel(user.role);
    });
  }

  function initPanels() {
    var links = $$('[data-panel-target]');
    var panels = $$('.dash-panel');
    if (!links.length || !panels.length) { return; }

    function activate(name, updateHash) {
      var found = false;

      panels.forEach(function (panel) {
        var isTarget = panel.getAttribute('data-panel') === name;
        panel.hidden = !isTarget;
        panel.classList.toggle('is-active', isTarget);
        if (isTarget) { found = true; }
      });

      if (!found) { return false; }

      links.forEach(function (link) {
        var isTarget = link.getAttribute('data-panel-target') === name;
        link.classList.toggle('is-active', isTarget);
        if (isTarget) {
          link.setAttribute('aria-current', 'page');
        } else {
          link.removeAttribute('aria-current');
        }
      });

      if (updateHash && window.location.hash !== '#' + name) {
        window.location.hash = name;
      }
      return true;
    }

    links.forEach(function (link) {
      link.addEventListener('click', function (event) {
        var name = link.getAttribute('data-panel-target');
        if (activate(name, true)) { event.preventDefault(); }
      });
    });

    var initial = (window.location.hash || '').replace('#', '');
    if (!initial || !activate(initial, false)) {
      activate(panels[0].getAttribute('data-panel'), false);
    }

    window.addEventListener('hashchange', function () {
      activate((window.location.hash || '').replace('#', ''), false);
    });

    return activate;
  }

  function resourcePreviewUrl(item) {
    return item.previewUrl ||
      (item.file && item.file.previewUrl) ||
      item.url ||
      (item.id ? '/api/resources/' + encodeURIComponent(item.id) + '/preview' : '');
  }

  function resourceDownloadUrl(item) {
    return item.downloadUrl ||
      (item.file && item.file.downloadUrl) ||
      (item.id ? '/api/resources/' + encodeURIComponent(item.id) + '/download' : '');
  }

  function renderResourceCards(items, list, options) {
    options = options || {};
    if (!list) { return; }
    list.innerHTML = '';
    if (!items || !items.length) {
      var empty = document.createElement('div');
      empty.className = 'placeholder-card';
      empty.innerHTML = '<span class="placeholder-icon" aria-hidden="true">&#128193;</span>' +
        '<h2>No resources found</h2><p class="muted">Try another search or upload a document.</p>';
      list.appendChild(empty);
      return;
    }

    items.forEach(function (item) {
      var card = document.createElement('article');
      card.className = 'announcement-card resource-card';
      var head = document.createElement('div');
      head.className = 'announcement-card-header';
      var title = document.createElement('h3');
      title.textContent = item.title;
      var badge = document.createElement('span');
      badge.className = 'announcement-badge';
      badge.textContent = item.branch || item.category || 'Resource';
      head.appendChild(title);
      head.appendChild(badge);

      var body = document.createElement('p');
      body.className = 'announcement-body';
      body.textContent = item.description || item.originalName || 'Document resource';
      var meta = document.createElement('div');
      meta.className = 'announcement-meta';
      meta.textContent = 'Uploaded ' + formatDate(item.createdAt) +
        (item.uploadedBy && item.uploadedBy.fullName ? ' • by ' + item.uploadedBy.fullName : '');
      var questionCount = document.createElement('div');
      questionCount.className = 'announcement-meta resource-question-count';
      questionCount.textContent = 'Questions: ' + (item.questionCount || 0);
      var rating = document.createElement('div');
      rating.className = 'announcement-meta resource-rating';
      rating.textContent = item.ratingCount
        ? '★ ' + item.rating + ' (' + item.ratingCount + ' rating' + (item.ratingCount === 1 ? '' : 's') + ')'
        : 'Not rated yet';

      var actions = document.createElement('div');
      actions.className = 'resource-actions';
      var link = document.createElement('a');
      var linkUrl = resourcePreviewUrl(item);
      if (linkUrl.endsWith('.ppt') || linkUrl.endsWith('.pptx')) {
        link.href = 'https://view.officeapps.live.com/op/view.aspx?src=' + encodeURIComponent(linkUrl);
      } else {
        link.href = linkUrl;
      }
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.className = 'btn btn-outline btn-sm';
      link.textContent = 'View';
      actions.appendChild(link);

      if (item.file || item.downloadUrl) {
        var download = document.createElement('a');
        download.href = resourceDownloadUrl(item);
        download.className = 'btn btn-outline btn-sm';
        download.textContent = 'Download';
        download.setAttribute('download', '');
        actions.appendChild(download);
      }

      var isUploader = currentUser && item.uploadedBy && String(item.uploadedBy.id) === String(currentUser.id);

      if (!isUploader && currentUser && currentUser.role !== 'admin') {
        var ratingSelect = document.createElement('select');
        ratingSelect.className = 'select resource-rating-select';
        ratingSelect.setAttribute('aria-label', 'Rate ' + item.title);
        for (var star = 1; star <= 5; star += 1) {
          var starOption = document.createElement('option');
          starOption.value = String(star);
          starOption.textContent = star + ' star' + (star === 1 ? '' : 's');
          ratingSelect.appendChild(starOption);
        }
        var rateButton = document.createElement('button');
        rateButton.type = 'button';
        rateButton.className = 'btn btn-outline btn-sm';
        rateButton.textContent = 'Rate resource';
        rateButton.addEventListener('click', function () {
          api('/api/resources/' + encodeURIComponent(item.id) + '/rating', {
            method: 'PUT',
            body: { rating: Number(ratingSelect.value) }
          }).then(function (result) {
            if (result.ok) {
              if (document.body.getAttribute('data-role') === 'student' ||
                  document.body.getAttribute('data-role') === 'faculty') {
                loadDashboardResources();
              }
            } else {
              window.alert(result.data.message || 'Unable to save this rating.');
            }
          });
        });
        actions.appendChild(ratingSelect);
        actions.appendChild(rateButton);
      }

      if (options.allowAsk !== false && !isUploader) {
        var ask = document.createElement('button');
        ask.type = 'button';
        ask.className = 'btn btn-primary btn-sm';
        ask.textContent = 'Ask question';
        ask.addEventListener('click', function () { openQuestionPrompt(item); });
        actions.appendChild(ask);
      }
      if (item.questionCount > 0) {
        var answer = document.createElement('button');
        answer.type = 'button';
        answer.className = 'btn btn-outline btn-sm';
        answer.textContent = 'Question list';
        answer.addEventListener('click', function () { openResourceAnswerDialog(item); });
        actions.appendChild(answer);
      }
      if (options.mine && item.id) {
        var remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'btn btn-outline btn-sm';
        remove.textContent = 'Delete';
        remove.addEventListener('click', function () {
          api('/api/resources/' + encodeURIComponent(item.id), { method: 'DELETE' }).then(function (result) {
            if (result.ok) { loadDashboardResources(); }
          });
        });
        actions.appendChild(remove);
      }
      card.appendChild(head);
      card.appendChild(body);
      card.appendChild(meta);
      card.appendChild(questionCount);
      card.appendChild(rating);
      card.appendChild(actions);
      list.appendChild(card);
    });
  }

  function openTextDialog(title, label, submitLabel, initialValue) {
    return new Promise(function (resolve) {
      var overlay = document.createElement('div');
      overlay.className = 'text-dialog-backdrop';
      overlay.innerHTML = '<div class="text-dialog" role="dialog" aria-modal="true" aria-labelledby="textDialogTitle">' +
        '<form class="text-dialog-form">' +
        '<h2 id="textDialogTitle"></h2>' +
        '<label class="text-dialog-label" for="textDialogInput"></label>' +
        '<textarea id="textDialogInput" class="input textarea" rows="4" required></textarea>' +
        '<div class="text-dialog-actions"><button type="button" class="btn btn-outline text-dialog-cancel">Cancel</button>' +
        '<button type="submit" class="btn btn-primary text-dialog-submit"></button></div>' +
        '</form></div>';
      document.body.appendChild(overlay);
      var form = overlay.querySelector('form');
      var input = overlay.querySelector('#textDialogInput');
      overlay.querySelector('#textDialogTitle').textContent = title;
      overlay.querySelector('.text-dialog-label').textContent = label;
      overlay.querySelector('.text-dialog-submit').textContent = submitLabel;
      input.value = initialValue || '';

      function close(value) {
        overlay.remove();
        resolve(value);
      }
      overlay.querySelector('.text-dialog-cancel').addEventListener('click', function () { close(''); });
      overlay.addEventListener('click', function (event) {
        if (event.target === overlay) close('');
      });
      form.addEventListener('submit', function (event) {
        event.preventDefault();
        var value = input.value.trim();
        if (value) close(value);
      });
      input.focus();
    });
  }

  function openQuestionPrompt(resource) {
    openTextDialog('Ask a question', 'Question about "' + resource.title + '"', 'Submit question').then(function (question) {
      if (!question) { return; }
      api('/api/questions', {
        method: 'POST',
        body: { resourceId: resource.id, text: question }
      }).then(function (result) {
        if (result.ok) {
          window.alert('Your question was sent. The uploader will be notified.');
          initQuestions();
        } else {
          window.alert(result.data.message || 'Unable to send the question.');
        }
      });
    });
  }

  function openResourceAnswerDialog(resource) {
    api('/api/questions').then(function (result) {
      if (!result.ok) {
        window.alert(result.data.message || 'Unable to load questions.');
        return;
      }
      var questions = (result.data.questions || []).filter(function (item) {
        return String(item.resourceId) === String(resource.id);
      });
      if (!questions.length) {
        window.alert('There are no questions asked on this resource yet.');
        return;
      }
      showResourceQuestionDialog(resource, questions);
    });
  }

  function showResourceQuestionDialog(resource, questions) {
    var overlay = document.createElement('div');
    overlay.className = 'text-dialog-backdrop';
    overlay.innerHTML = '<div class="text-dialog resource-answer-dialog" role="dialog" aria-modal="true" aria-labelledby="resourceAnswerTitle" style="max-width: 600px; width: 90%; max-height: 90vh; overflow-y: auto;">' +
      '<h2 id="resourceAnswerTitle"></h2><p class="muted">Questions asked by users and their answers are listed below.</p>' +
      '<div class="resource-question-options"></div>' +
      '<div class="text-dialog-actions"><button type="button" class="btn btn-outline resource-answer-close">Close</button></div></div>';
    document.body.appendChild(overlay);
    overlay.querySelector('#resourceAnswerTitle').textContent = resource.title;
    var options = overlay.querySelector('.resource-question-options');

    questions.forEach(function (item) {
      var isOwnQuestion = currentUser && item.askedBy && String(item.askedBy.id) === String(currentUser.id);
      var questionCard = document.createElement('div');
      questionCard.className = 'resource-question-option card';
      questionCard.style.marginBottom = '1rem';
      questionCard.style.padding = '1rem';
      
      var questionText = document.createElement('p');
      questionText.className = 'announcement-body';
      questionText.style.fontWeight = 'bold';
      questionText.style.fontSize = '1.1rem';
      questionText.textContent = item.text || item.question || '';
      
      var askedBy = document.createElement('p');
      askedBy.className = 'announcement-meta';
      askedBy.textContent = 'Asked by ' + (item.askedBy && item.askedBy.fullName ? item.askedBy.fullName : 'Student');
      
      questionCard.appendChild(questionText);
      questionCard.appendChild(askedBy);

      var answers = item.answers || (item.answer ? [{
        text: item.answer,
        answeredBy: item.answeredBy
      }] : []);
      
      if (answers.length > 0) {
        var answerList = document.createElement('div');
        answerList.style.marginTop = '0.5rem';
        answerList.style.paddingTop = '0.5rem';
        answerList.style.borderTop = '1px solid #eee';
        answers.forEach(function(answer) {
          var ansNode = document.createElement('p');
          ansNode.style.margin = '0.25rem 0';
          var author = document.createElement('strong');
          author.textContent = (answer.answeredBy && answer.answeredBy.fullName ? answer.answeredBy.fullName : 'Answer') + ': ';
          ansNode.appendChild(author);
          ansNode.appendChild(document.createTextNode(answer.text || ''));
          answerList.appendChild(ansNode);
        });
        questionCard.appendChild(answerList);
      } else {
        var noAns = document.createElement('p');
        noAns.className = 'muted small';
        noAns.style.marginTop = '0.5rem';
        noAns.textContent = 'no answers yet';
        questionCard.appendChild(noAns);
      }

      if (!isOwnQuestion) {
        var actionDiv = document.createElement('div');
        actionDiv.style.marginTop = '1rem';
        var answerButton = document.createElement('button');
        answerButton.type = 'button';
        answerButton.className = 'btn btn-primary btn-sm';
        answerButton.textContent = 'Answer';
        answerButton.addEventListener('click', function () {
          overlay.remove();
          openTextDialog('Answer question', questionText.textContent, 'Submit answer').then(function (text) {
            if (!text) { return; }
            api('/api/questions/' + encodeURIComponent(item.id) + '/answers', {
              method: 'POST',
              body: { text: text }
            }).then(function (answerResult) {
              if (answerResult.ok) {
                window.alert('Your answer was submitted.');
                initQuestions();
                loadDashboardResources();
              } else {
                window.alert(answerResult.data.message || 'Unable to post answer.');
              }
            });
          });
        });
        actionDiv.appendChild(answerButton);
        questionCard.appendChild(actionDiv);
      }
      options.appendChild(questionCard);
    });

    overlay.querySelector('.resource-answer-close').addEventListener('click', function () { overlay.remove(); });
    overlay.addEventListener('click', function (event) {
      if (event.target === overlay) { overlay.remove(); }
    });
  }

  function loadDashboardResources(search) {
    var page = document.body.getAttribute('data-page');
    var mine = page === 'faculty-dashboard' ? document.getElementById('resourceList') :
      document.getElementById('studentResourceList');
    if (!mine) { return; }
    var path = '/api/resources/my' + (search ? '?search=' + encodeURIComponent(search) : '');
    api(path).then(function (result) {
      if (result.ok) { renderResourceCards(result.data.resources || [], mine, { mine: true }); }
    });
  }

  function initResourceUpload() {
    var form = document.getElementById('resourceUploadForm') || document.getElementById('studentResourceUploadForm');
    var list = document.getElementById('resourceList') || document.getElementById('studentResourceList');
    var alertBox = document.getElementById('resourceFormAlert') || document.getElementById('studentResourceFormAlert');
    if (!form || !list) { return; }

    var mySearchForm = document.getElementById('myResourceSearchForm');
    if (mySearchForm) {
      mySearchForm.addEventListener('submit', function (event) {
        event.preventDefault();
        loadDashboardResources(mySearchForm.elements.search.value.trim());
      });
    }

    function showFormAlert(type, message) {
      if (!alertBox) { return; }
      alertBox.className = 'alert alert-' + type;
      alertBox.textContent = message;
      alertBox.hidden = false;
    }

    function hideFormAlert() {
      if (!alertBox) { return; }
      alertBox.hidden = true;
      alertBox.textContent = '';
      alertBox.className = 'alert';
    }

    form.addEventListener('submit', function (event) {
      event.preventDefault();
      hideFormAlert();

      var title = form.elements.title.value.trim();
      var description = form.elements.description.value.trim();
      var branch = form.elements.branch.value;
      var subject = form.elements.subject ? form.elements.subject.value.trim() : '';
      var semester = form.elements.semester ? form.elements.semester.value : '';
      var category = form.elements.category ? form.elements.category.value : 'Notes';
      var file = form.elements.file.files[0];

      if (!title || title.length < 3) {
        showFormAlert('error', 'Please enter a resource title with at least 3 characters.');
        return;
      }
      if (!description || description.length < 10) {
        showFormAlert('error', 'Please add a longer description so students know what the resource contains.');
        return;
      }
      if (!branch) {
        showFormAlert('error', 'Please choose a branch.');
        return;
      }
      if (!file) {
        showFormAlert('error', 'Please choose a document to upload.');
        return;
      }

      var submit = form.querySelector('button[type="submit"]');
      setBusy(submit, true, 'Uploading…');

      var formData = new FormData();
      formData.append('title', title);
      formData.append('description', description);
      formData.append('branch', branch);
      formData.append('subject', subject);
      formData.append('semester', semester);
      formData.append('category', category);
      formData.append('file', file);

      api('/api/resources', { method: 'POST', body: formData }).then(function (result) {
        setBusy(submit, false);

        if (!result.ok) {
          showFormAlert('error', result.data.message || 'Unable to upload the resource.');
          return;
        }

        form.reset();
        showFormAlert('success', result.data.message || 'Resource uploaded successfully.');
        loadDashboardResources();
      });
    });

    loadDashboardResources();
  }

  function initResourceSearch() {
    var form = document.getElementById('resourceSearchForm');
    var list = document.getElementById('availableResourceList');
    var status = document.getElementById('resourceSearchStatus');
    if (!form || !list) { return; }

    function search() {
      var params = {
        search: form.elements.search.value.trim(),
        branch: form.elements.branch.value,
        subject: form.elements.subject ? form.elements.subject.value.trim() : '',
        semester: form.elements.semester ? form.elements.semester.value : '',
        category: form.elements.category ? form.elements.category.value : ''
      };
      if (form.elements.sort) {
        params.sort = form.elements.sort.value;
      }
      var query = new URLSearchParams(params);
      api('/api/resources?' + query.toString()).then(function (result) {
        if (!result.ok) {
          if (status) { status.textContent = result.data.message || 'Search failed.'; }
          return;
        }
        var resources = result.data.resources || [];
        if (status) { status.textContent = resources.length + ' resource(s) found.'; }
        renderResourceCards(resources, list, {
          mine: false,
          allowAsk: document.body.getAttribute('data-role') !== 'faculty'
        });
      });
    }
    form.addEventListener('submit', function (event) {
      event.preventDefault();
      search();
    });
    search();
  }

  function renderQuestions(items, list, canAnswer) {
    if (!list) { return; }
    list.innerHTML = '';
    if (!items || !items.length) {
      list.innerHTML = '<div class="placeholder-card"><span class="placeholder-icon" aria-hidden="true">&#10067;</span><h2>No questions yet</h2><p class="muted">Questions and answers will appear here.</p></div>';
      return;
    }
    items.forEach(function (item) {
      var card = document.createElement('article');
      card.className = 'announcement-card question-card';
      var title = document.createElement('h3');
      title.textContent = item.resource && item.resource.title ? item.resource.title : 'Resource question';
      var question = document.createElement('p');
      question.className = 'announcement-body';
      question.textContent = item.text || item.question || '';
      var meta = document.createElement('div');
      meta.className = 'announcement-meta';
      meta.textContent = (item.askedBy && item.askedBy.fullName ? item.askedBy.fullName : 'Student') +
        ' • ' + formatDate(item.createdAt);
      card.appendChild(title);
      card.appendChild(question);
      card.appendChild(meta);

      var answers = item.answers || (item.answer ? [{
        text: item.answer,
        answeredBy: item.answeredBy
      }] : []);
      if (answers.length === 0) {
        var noAns = document.createElement('p');
        noAns.className = 'muted small';
        noAns.style.marginTop = '0.5rem';
        noAns.textContent = 'no answers yet';
        card.appendChild(noAns);
      } else {
        answers.forEach(function (answer) {
          var answerNode = document.createElement('p');
          answerNode.className = 'question-answer';
          answerNode.textContent = (answer.answeredBy && answer.answeredBy.fullName ? answer.answeredBy.fullName : 'Answer') + ': ' + answer.text;
          card.appendChild(answerNode);
          if (currentUser && answer.answeredBy &&
              String(answer.answeredBy.id) === String(currentUser.id)) {
            var answerActions = document.createElement('div');
            answerActions.className = 'resource-actions';
            var editAnswer = document.createElement('button');
            editAnswer.type = 'button';
            editAnswer.className = 'btn btn-outline btn-sm';
            editAnswer.textContent = 'Edit answer';
            editAnswer.addEventListener('click', function () {
              openTextDialog('Edit answer', 'Your answer', 'Save answer', answer.text)
                .then(function (text) {
                  if (!text) { return; }
                  api('/api/questions/' + encodeURIComponent(item.id) + '/answers', {
                    method: 'PUT',
                    body: { text: text }
                  }).then(function (result) {
                    if (result.ok) { initQuestions(); }
                    else { window.alert(result.data.message || 'Unable to update answer.'); }
                  });
                });
            });
            var deleteAnswer = document.createElement('button');
            deleteAnswer.type = 'button';
            deleteAnswer.className = 'btn btn-outline btn-sm';
            deleteAnswer.textContent = 'Delete answer';
            deleteAnswer.addEventListener('click', function () {
              if (!window.confirm('Delete your answer?')) { return; }
              api('/api/questions/' + encodeURIComponent(item.id) + '/answers', {
                method: 'DELETE'
              }).then(function (result) {
                if (result.ok) { initQuestions(); }
                else { window.alert(result.data.message || 'Unable to delete answer.'); }
              });
            });
            answerActions.appendChild(editAnswer);
            answerActions.appendChild(deleteAnswer);
            card.appendChild(answerActions);
          }
        });
        var helpfulMeta = document.createElement('p');
        helpfulMeta.className = 'announcement-meta';
        helpfulMeta.textContent = (item.helpfulCount || 0) + ' helpful response(s)';
        card.appendChild(helpfulMeta);
        if (currentUser && !item.myHelpfulVote && item.answeredBy &&
            String(item.answeredBy.id) !== String(currentUser.id)) {
          var helpfulButton = document.createElement('button');
          helpfulButton.type = 'button';
          helpfulButton.className = 'btn btn-outline btn-sm';
          helpfulButton.textContent = 'Mark answer helpful';
          helpfulButton.addEventListener('click', function () {
            api('/api/questions/' + encodeURIComponent(item.id) + '/helpful', {
              method: 'POST',
              body: {}
            }).then(function (result) {
              if (result.ok) { initQuestions(); }
              else { window.alert(result.data.message || 'Unable to record helpful feedback.'); }
            });
          });
          card.appendChild(helpfulButton);
        }
      }

      var isOwnQuestion = currentUser && item.askedBy && String(item.askedBy.id) === String(currentUser.id);
      if (isOwnQuestion && item.answer) {
        var acceptButton = document.createElement('button');
        acceptButton.type = 'button';
        acceptButton.className = item.accepted ? 'btn btn-outline btn-sm' : 'btn btn-primary btn-sm';
        acceptButton.textContent = item.accepted ? 'Helpful answer accepted' : 'Mark answer helpful';
        acceptButton.disabled = Boolean(item.accepted);
        acceptButton.addEventListener('click', function () {
          api('/api/questions/' + encodeURIComponent(item.id) + '/accept', {
            method: 'PUT',
            body: { accepted: true }
          }).then(function (result) {
            if (result.ok) { initQuestions(); }
            else { window.alert(result.data.message || 'Unable to mark this answer helpful.'); }
          });
        });
        card.appendChild(acceptButton);
      }
      if (canAnswer && item.id && !isOwnQuestion) {
        var answerButton = document.createElement('button');
        answerButton.type = 'button';
        answerButton.className = 'btn btn-primary btn-sm';
        answerButton.textContent = 'Answer';
        answerButton.addEventListener('click', function () {
          openTextDialog('Answer question', 'Your answer', 'Submit answer').then(function (text) {
            if (!text) { return; }
            api('/api/questions/' + encodeURIComponent(item.id) + '/answers', {
              method: 'POST',
              body: { text: text }
            }).then(function (result) {
              if (result.ok) { initQuestions(); } else { window.alert(result.data.message || 'Unable to post answer.'); }
            });
          });
        });
        card.appendChild(answerButton);
      }
      list.appendChild(card);
    });
  }

  function initQuestions() {
    var studentList = document.getElementById('studentQuestionList');
    var studentAnswers = document.getElementById('studentAnswerList');
    var facultyList = document.getElementById('facultyQuestionList');
    var facultyAnsweredList = document.getElementById('facultyAnsweredQuestionList');
    if (!studentList && !studentAnswers && !facultyList && !facultyAnsweredList) { return; }

    api('/api/questions').then(function (result) {
      if (!result.ok) { return; }
      var questions = result.data.questions || [];
      var role = document.body.getAttribute('data-role');
      if (role === 'faculty') {
        renderQuestions(questions.filter(function (item) { return !item.answer; }), facultyList, true);
        renderQuestions(questions.filter(function (item) { return Boolean(item.answer); }), facultyAnsweredList, false);
      } else {
        var myQuestions = questions.filter(function (item) {
          return item.askedBy && String(item.askedBy.id) === String(currentUser.id);
        });
        var myAnswers = questions.filter(function (item) {
          var answeredByMe = false;
          if (item.answeredBy && String(item.answeredBy.id) === String(currentUser.id)) answeredByMe = true;
          if (item.answers) {
            item.answers.forEach(function (a) {
              if (a.answeredBy && String(a.answeredBy.id) === String(currentUser.id)) answeredByMe = true;
            });
          }
          return answeredByMe;
        });
        renderQuestions(myQuestions, studentList, true);
        renderQuestions(myAnswers, studentAnswers, false);
      }
    });
  }

  function renderLeaderboard(container, leaders, title, includeRank) {
    if (!container) { return; }
    var section = document.createElement('section');
    var heading = document.createElement('h3');
    heading.textContent = title;
    section.appendChild(heading);
    var table = document.createElement('table');
    table.className = 'table';
    var head = document.createElement('thead');
    var headerRow = document.createElement('tr');
    (includeRank ? ['Rank', 'Name', 'Rating', 'Points', 'Resources', 'Questions answered', 'Helpful answers', 'Accepted answers', 'Badges'] :
      ['Name', 'Rating', 'Points', 'Resources', 'Questions answered', 'Helpful answers', 'Accepted answers', 'Badges']).forEach(function (label) {
      var th = document.createElement('th');
      th.scope = 'col';
      th.textContent = label;
      headerRow.appendChild(th);
    });
    head.appendChild(headerRow);
    table.appendChild(head);
    var body = document.createElement('tbody');
    (leaders || []).forEach(function (leader, index) {
      var row = document.createElement('tr');
      var cells = [];
      if (includeRank) { cells.push(String(leader.rank || index + 1)); }
      cells.push(leader.name || 'CampusHub member');
      cells.push(leader.rating ? '★ ' + Number(leader.rating).toFixed(1) : 'Not rated');
      cells.push(String(leader.points || 0));
      cells.push(String(leader.resourcesUploaded || 0));
      cells.push(String(leader.questionsAnswered || 0));
      cells.push(String(leader.helpfulAnswers || 0));
      cells.push(String(leader.acceptedAnswers || 0));
      cells.push((leader.badges || []).map(function (badge) { return badge.name; }).join(', ') || '—');
      cells.forEach(function (value) {
        var cell = document.createElement('td');
        cell.textContent = value;
        row.appendChild(cell);
      });
      body.appendChild(row);
    });
    if (!leaders || !leaders.length) {
      var emptyRow = document.createElement('tr');
      var emptyCell = document.createElement('td');
      emptyCell.colSpan = includeRank ? 9 : 8;
      emptyCell.className = 'muted';
      emptyCell.textContent = 'No contributions are available yet.';
      emptyRow.appendChild(emptyCell);
      body.appendChild(emptyRow);
    }
    table.appendChild(body);
    var wrapper = document.createElement('div');
    wrapper.className = 'table-wrap';
    wrapper.appendChild(table);
    section.appendChild(wrapper);
    container.appendChild(section);
  }

  function renderBarChart(container, entries, labelKey, valueKey, emptyText) {
    if (!container) { return; }
    container.innerHTML = '';
    var max = (entries || []).reduce(function (value, entry) {
      return Math.max(value, Number(entry[valueKey]) || 0);
    }, 0);
    if (!entries || !entries.length || !max) {
      var empty = document.createElement('p');
      empty.className = 'muted small';
      empty.textContent = emptyText || 'Contribution data will appear here as actions are recorded.';
      container.appendChild(empty);
      return;
    }
    entries.forEach(function (entry) {
      var row = document.createElement('div');
      row.className = 'leadership-bar-row';
      var label = document.createElement('span');
      label.className = 'leadership-bar-label';
      label.textContent = entry[labelKey];
      var track = document.createElement('span');
      track.className = 'leadership-bar-track';
      var bar = document.createElement('span');
      bar.className = 'leadership-bar';
      bar.style.width = Math.max(3, (Number(entry[valueKey]) / max) * 100) + '%';
      track.appendChild(bar);
      var value = document.createElement('strong');
      value.textContent = String(entry[valueKey]);
      row.appendChild(label);
      row.appendChild(track);
      row.appendChild(value);
      container.appendChild(row);
    });
  }

  function renderGrowthChart(container, entries) {
    if (!container) { return; }
    container.innerHTML = '';
    var max = (entries || []).reduce(function (value, entry) {
      return Math.max(value, Number(entry.points) || 0);
    }, 0);
    if (!entries || !entries.length || !max) {
      var empty = document.createElement('p');
      empty.className = 'muted small';
      empty.textContent = 'Monthly contribution points will appear here as actions are recorded.';
      container.appendChild(empty);
      return;
    }
    container.className = 'leadership-growth-chart';
    entries.forEach(function (entry) {
      var column = document.createElement('div');
      column.className = 'leadership-growth-column';
      var value = document.createElement('strong');
      value.textContent = String(entry.points);
      var track = document.createElement('span');
      track.className = 'leadership-growth-track';
      var bar = document.createElement('span');
      bar.className = 'leadership-growth-bar';
      bar.style.height = Math.max(4, (Number(entry.points) / max) * 100) + '%';
      track.appendChild(bar);
      var month = document.createElement('span');
      month.className = 'leadership-growth-month';
      month.textContent = entry.month;
      column.appendChild(value);
      column.appendChild(track);
      column.appendChild(month);
      container.appendChild(column);
    });
  }

  function renderLeadershipProfile(data) {
    var root = document.getElementById('leadershipDashboard');
    if (!root) { return; }
    root.innerHTML = '';
    var user = data.profile.user;
    var stats = data.profile.stats;
    var statGrid = document.createElement('div');
    statGrid.className = 'stat-grid leadership-stat-grid';
    [
      ['User', user.name],
      ['Role', user.role.charAt(0).toUpperCase() + user.role.slice(1)],
      ['Star rating', stats.rating ? '★ ' + Number(stats.rating).toFixed(1) : 'Not rated'],
      ['Contribution points', stats.points],
      ['Resources uploaded', stats.resourcesUploaded],
      ['Questions answered', stats.questionsAnswered],
      ['Helpful answers', stats.helpfulAnswers],
    ].forEach(function (metric) {
      var card = document.createElement('article');
      card.className = 'stat-card';
      var label = document.createElement('span');
      label.className = 'stat-label';
      label.textContent = metric[0];
      var value = document.createElement('span');
      value.className = 'stat-value';
      value.textContent = String(metric[1]);
      card.appendChild(label);
      card.appendChild(value);
      statGrid.appendChild(card);
    });
    root.appendChild(statGrid);

    var badgesCard = document.createElement('section');
    badgesCard.className = 'card';
    var badgesHeading = document.createElement('h2');
    badgesHeading.className = 'card-title';
    badgesHeading.textContent = 'Badges and rewards';
    badgesCard.appendChild(badgesHeading);
    var badgesList = document.createElement('div');
    badgesList.className = 'leadership-badges';
    (stats.badges || []).forEach(function (badge) {
      var badgeElement = document.createElement('span');
      badgeElement.className = 'leadership-badge';
      badgeElement.textContent = badge.name;
      badgesList.appendChild(badgeElement);
    });
    if (!stats.badges || !stats.badges.length) {
      var noBadges = document.createElement('p');
      noBadges.className = 'muted';
      noBadges.textContent = 'Keep contributing to earn your first badge.';
      badgesList.appendChild(noBadges);
    }
    badgesCard.appendChild(badgesList);
    root.appendChild(badgesCard);

    var graphCard = document.createElement('section');
    graphCard.className = 'card';
    var graphHeading = document.createElement('h2');
    graphHeading.className = 'card-title';
    graphHeading.textContent = 'Contribution growth by month (points)';
    graphCard.appendChild(graphHeading);
    var growthGraph = document.createElement('div');
    renderGrowthChart(growthGraph, stats.monthlyPoints || []);
    graphCard.appendChild(growthGraph);
    root.appendChild(graphCard);

    var breakdownCard = document.createElement('section');
    breakdownCard.className = 'card';
    var breakdownHeading = document.createElement('h2');
    breakdownHeading.className = 'card-title';
    breakdownHeading.textContent = 'Contribution breakdown';
    breakdownCard.appendChild(breakdownHeading);
    var breakdown = [
      { label: 'Resources uploaded', value: stats.breakdown.resources },
      { label: 'Questions answered', value: stats.breakdown.questionsAnswered },
      { label: 'Helpful answers', value: stats.breakdown.helpfulAnswers },
      { label: 'Accepted answers', value: stats.breakdown.acceptedAnswers },
    ];
    var breakdownChart = document.createElement('div');
    breakdownChart.className = 'leadership-bars';
    renderBarChart(breakdownChart, breakdown, 'label', 'value');
    breakdownCard.appendChild(breakdownChart);
    root.appendChild(breakdownCard);

    var historyCard = document.createElement('section');
    historyCard.className = 'card';
    var historyHeading = document.createElement('h2');
    historyHeading.className = 'card-title';
    historyHeading.textContent = 'Contribution history';
    historyCard.appendChild(historyHeading);
    var history = document.createElement('ul');
    history.className = 'leadership-history';
    (stats.history || []).forEach(function (event) {
      var row = document.createElement('li');
      row.textContent = event.type.replace(/_/g, ' ') + ' — +' + event.points +
        ' points · ' + formatDate(event.createdAt);
      history.appendChild(row);
    });
    if (!stats.history || !stats.history.length) {
      var noHistory = document.createElement('li');
      noHistory.className = 'muted';
      noHistory.textContent = 'Your contribution activity will be listed here.';
      history.appendChild(noHistory);
    }
    historyCard.appendChild(history);
    root.appendChild(historyCard);

    var leaderboards = document.createElement('div');
    leaderboards.className = 'leadership-columns';
    var students = document.createElement('section');
    students.className = 'card';
    var faculty = document.createElement('section');
    faculty.className = 'card';
    renderLeaderboard(students, data.leaderboards.students, 'Student Leadership', true);
    renderLeaderboard(faculty, data.leaderboards.faculty, 'Faculty Leadership', true);
    leaderboards.appendChild(students);
    leaderboards.appendChild(faculty);
    root.appendChild(leaderboards);

    var comparisonCharts = document.createElement('div');
    comparisonCharts.className = 'leadership-columns';
    [
      { title: 'Student contribution points', leaders: data.leaderboards.students },
      { title: 'Faculty contribution points', leaders: data.leaderboards.faculty },
    ].forEach(function (group) {
      var chartCard = document.createElement('section');
      chartCard.className = 'card';
      var chartTitle = document.createElement('h2');
      chartTitle.className = 'card-title';
      chartTitle.textContent = group.title;
      chartCard.appendChild(chartTitle);
      var chart = document.createElement('div');
      chart.className = 'leadership-bars';
      renderBarChart(chart, (group.leaders || []).slice(0, 10), 'name', 'points');
      chartCard.appendChild(chart);
      comparisonCharts.appendChild(chartCard);
    });
    root.appendChild(comparisonCharts);
  }

  function initAdminManagement() {
    var usersBody = document.getElementById('adminUsersTableBody');
    var usersSearch = document.getElementById('adminUserSearch');
    var resourcesTarget = document.getElementById('adminResourcesList');
    var questionsTarget = document.getElementById('adminQuestionsList');
    var categoriesTarget = document.getElementById('adminCategoriesList');
    var users = [];

    function fail(target, message) {
      if (target) {
        target.textContent = message;
      }
    }

    function renderUsers() {
      if (!usersBody) { return; }
      var term = usersSearch ? usersSearch.value.trim().toLowerCase() : '';
      var currentUsers = usersSearch && usersSearch._campusUsers ? usersSearch._campusUsers : users;
      usersBody.innerHTML = '';
      var visible = currentUsers.filter(function (user) {
        return !term || [user.fullName, user.email, user.role, user.branch]
          .some(function (value) { return String(value || '').toLowerCase().indexOf(term) !== -1; });
      });
      if (!visible.length) {
        var empty = document.createElement('tr');
        var cell = document.createElement('td');
        cell.colSpan = 6;
        cell.className = 'muted';
        cell.textContent = 'No matching users.';
        empty.appendChild(cell);
        usersBody.appendChild(empty);
        return;
      }
      visible.forEach(function (user) {
        var row = document.createElement('tr');
        [user.fullName, user.email, roleLabel(user.role), user.branch || '—',
          formatDate(user.createdAt)].forEach(function (value) {
          var cell = document.createElement('td');
          cell.textContent = String(value || '');
          row.appendChild(cell);
        });
        var actionCell = document.createElement('td');
        if (user.role !== 'admin') {
          var toggle = document.createElement('button');
          toggle.type = 'button';
          toggle.className = 'btn btn-outline btn-sm';
          toggle.textContent = user.active ? 'Disable' : 'Enable';
          toggle.addEventListener('click', function () {
            api('/api/admin/users/' + encodeURIComponent(user.id), {
              method: 'PATCH',
              body: { active: !user.active }
            }).then(function (result) {
              if (result.ok) { initAdminManagement(); }
              else { window.alert(result.data.message || 'Unable to update this account.'); }
            });
          });
          actionCell.appendChild(toggle);
        } else {
          actionCell.textContent = user.active ? 'Active admin' : 'Disabled';
        }
        row.appendChild(actionCell);
        usersBody.appendChild(row);
      });
    }

    api('/api/admin/overview').then(function (result) {
      if (!result.ok) {
        fail(usersBody, result.data.message || 'Unable to load admin data.');
        return;
      }
      users = result.data.users || [];
      if (usersSearch) { usersSearch._campusUsers = users; }
      renderUsers();
      var stats = result.data.stats || {};
      [
        ['adminStatUsers', stats.totalUsers],
        ['adminStatStudents', stats.students],
        ['adminStatFaculty', stats.faculty],
        ['adminStatResources', stats.resources],
        ['adminStatAnswers', stats.answers],
        ['adminStatUploads', stats.uploadsThisMonth]
      ].forEach(function (entry) {
        var target = document.getElementById(entry[0]);
        if (target) { target.textContent = String(entry[1] || 0); }
      });
      var statsStatus = document.getElementById('adminStatsStatus');
      if (statsStatus) {
        statsStatus.textContent = (stats.questions || 0) + ' active questions · ' +
          (stats.admins || 0) + ' administrators';
      }

      if (resourcesTarget) {
        resourcesTarget.innerHTML = '';
        (result.data.resources || []).forEach(function (resource) {
          var card = document.createElement('article');
          card.className = 'announcement-card resource-card';
          var title = document.createElement('h2');
          title.className = 'card-title';
          title.textContent = resource.title || 'Untitled resource';
          var meta = document.createElement('p');
          meta.className = 'muted small';
          meta.textContent = (resource.uploadedBy ? resource.uploadedBy.fullName : 'Unknown uploader') +
            ' · ' + (resource.branch || 'No branch') + ' · ' +
            (resource.subject || 'No subject') + (resource.isActive ? '' : ' · Removed');
          var actions = document.createElement('div');
          actions.className = 'resource-actions';
          if (resource.file && resource.file.previewUrl) {
            var view = document.createElement('a');
            view.className = 'btn btn-outline btn-sm';
            view.href = resource.file.previewUrl;
            view.target = '_blank';
            view.rel = 'noopener noreferrer';
            view.textContent = 'View';
            actions.appendChild(view);
          }
          if (resource.isActive) {
            var remove = document.createElement('button');
            remove.type = 'button';
            remove.className = 'btn btn-outline btn-sm';
            remove.textContent = 'Remove';
            remove.addEventListener('click', function () {
              if (!window.confirm('Remove this resource?')) { return; }
              api('/api/resources/' + encodeURIComponent(resource.id), { method: 'DELETE' })
                .then(function (removed) {
                  if (removed.ok) { initAdminManagement(); }
                  else { window.alert(removed.data.message || 'Unable to remove resource.'); }
                });
            });
            actions.appendChild(remove);
          }
          card.appendChild(title);
          card.appendChild(meta);
          card.appendChild(actions);
          resourcesTarget.appendChild(card);
        });
        if (!resourcesTarget.children.length) {
          fail(resourcesTarget, 'No resources have been shared yet.');
        }
      }

      if (questionsTarget) {
        questionsTarget.innerHTML = '';
        (result.data.questions || []).forEach(function (question) {
          var card = document.createElement('article');
          card.className = 'card question-card';
          var title = document.createElement('h2');
          title.className = 'card-title';
          title.textContent = question.text;
          var asked = document.createElement('p');
          asked.className = 'muted small';
          asked.textContent = 'Asked by ' + (question.askedBy ? question.askedBy.fullName : 'Unknown user') +
            (question.resource ? ' · ' + question.resource.title : '');
          var answer = document.createElement('p');
          answer.textContent = question.answer ? 'Answer: ' + question.answer : 'No answer submitted yet.';
          var remove = document.createElement('button');
          remove.type = 'button';
          remove.className = 'btn btn-outline btn-sm';
          remove.textContent = 'Remove question';
          remove.addEventListener('click', function () {
            if (!window.confirm('Remove this question and its related contribution points?')) { return; }
            api('/api/questions/' + encodeURIComponent(question.id), { method: 'DELETE' })
              .then(function (removed) {
                if (removed.ok) { initAdminManagement(); }
                else { window.alert(removed.data.message || 'Unable to remove question.'); }
              });
          });
          card.appendChild(title);
          card.appendChild(asked);
          card.appendChild(answer);
          card.appendChild(remove);
          questionsTarget.appendChild(card);
        });
        if (!questionsTarget.children.length) {
          fail(questionsTarget, 'No questions have been submitted.');
        }
      }
    });

    api('/api/admin/categories').then(function (result) {
      if (!categoriesTarget) { return; }
      categoriesTarget.innerHTML = '';
      if (!result.ok || !(result.data.categories || []).length) {
        fail(categoriesTarget, result.ok ? 'No resource categories have been used yet.' :
          (result.data.message || 'Unable to load categories.'));
        return;
      }
      (result.data.categories || []).forEach(function (category) {
        var badge = document.createElement('span');
        badge.className = 'leadership-badge';
        badge.textContent = category;
        categoriesTarget.appendChild(badge);
      });
    });

    if (usersSearch && !usersSearch.dataset.bound) {
      usersSearch.dataset.bound = 'true';
      usersSearch.addEventListener('input', renderUsers);
    }
  }

  function initLeadership() {
    var role = document.body.getAttribute('data-role');
    var endpoint = role === 'admin' ? '/api/admin/leadership' : '/api/leadership';
    if (role === 'admin') {
      initAdminLeadership();
      return;
    }
    api(endpoint).then(function (result) {
      if (result.ok) { renderLeadershipProfile(result.data); }
      else {
        var root = document.getElementById('leadershipDashboard');
        if (root) { root.textContent = result.data.message || 'Unable to load leadership information.'; }
      }
    });
  }

  function initAdminLeadership() {
    var branchSelect = document.getElementById('studentBranchRankingFilter');
    var rankingTarget = document.getElementById('branchStudentRankings');
    var graph = document.getElementById('branchRatingGraph');
    var studentBoard = document.getElementById('adminStudentLeaderboard');
    var facultyBoard = document.getElementById('adminFacultyLeaderboard');
    var suspiciousTarget = document.getElementById('suspiciousLeadershipActivity');
    var ratingsTarget = document.getElementById('adminResourceRatings');
    var reviewTarget = document.getElementById('adminContributionReview');
    if (!branchSelect) { return; }

    function load(branch) {
      var url = '/api/admin/leadership' + (branch ? '?branch=' + encodeURIComponent(branch) : '');
      api(url).then(function (result) {
        if (!result.ok) {
          if (rankingTarget) { rankingTarget.textContent = result.data.message || 'Unable to load rankings.'; }
          return;
        }
        var data = result.data;
        var current = branchSelect.value;
        branchSelect.innerHTML = '';
        var all = document.createElement('option');
        all.value = '';
        all.textContent = 'All branches';
        branchSelect.appendChild(all);
        (data.branches || []).forEach(function (branchName) {
          var option = document.createElement('option');
          option.value = branchName;
          option.textContent = branchName;
          branchSelect.appendChild(option);
        });
        if ((data.branches || []).indexOf(current) !== -1) { branchSelect.value = current; }
        if (rankingTarget) {
          rankingTarget.innerHTML = '';
          (data.branchRankings || []).forEach(function (group) {
            var card = document.createElement('section');
            card.className = 'card';
            renderLeaderboard(card, group.students, group.branch + ' — Student Rankings', true);
            rankingTarget.appendChild(card);
          });
          if (!data.branchRankings || !data.branchRankings.length) {
            rankingTarget.textContent = 'No students have registered with a branch yet.';
          }
        }
        var graphRows = [];
        (data.branchRankings || []).forEach(function (group) {
          group.students.forEach(function (student) {
            graphRows.push({ name: student.name + ' (' + group.branch + ')', rating: student.rating });
          });
        });
        renderBarChart(graph, graphRows.slice(0, 20), 'name', 'rating', 'No student ratings are available yet.');
        renderLeaderboard(studentBoard, data.leaderboards.students, 'Student Leaderboard', true);
        renderLeaderboard(facultyBoard, data.leaderboards.faculty, 'Faculty Leaderboard', true);
      });
    }

    if (!branchSelect.dataset.bound) {
      branchSelect.dataset.bound = 'true';
      branchSelect.addEventListener('change', function () { load(branchSelect.value); });
    }
    load(branchSelect.value);

    api('/api/admin/leadership/review').then(function (result) {
      if (!result.ok) {
        if (reviewTarget) { reviewTarget.textContent = result.data.message || 'Unable to load contribution review.'; }
        return;
      }
      if (suspiciousTarget) {
        suspiciousTarget.innerHTML = '';
        if (!result.data.suspicious.length) {
          suspiciousTarget.textContent = 'No activity currently meets the suspicious-activity review threshold.';
        } else {
          result.data.suspicious.forEach(function (entry) {
            var item = document.createElement('p');
            item.textContent = entry.user.name + ' submitted ' + entry.ratingsInLastHour + ' resource ratings in the last hour.';
            suspiciousTarget.appendChild(item);
          });
        }
      }
      if (ratingsTarget) {
        ratingsTarget.innerHTML = '';
        var ratingTable = document.createElement('table');
        ratingTable.className = 'table';
        var ratingHead = document.createElement('thead');
        var ratingHeader = document.createElement('tr');
        ['Resource', 'Contributor', 'Rated by', 'Rating', 'Updated'].forEach(function (label) {
          var cell = document.createElement('th');
          cell.scope = 'col';
          cell.textContent = label;
          ratingHeader.appendChild(cell);
        });
        ratingHead.appendChild(ratingHeader);
        ratingTable.appendChild(ratingHead);
        var ratingBody = document.createElement('tbody');
        (result.data.resourceRatings || []).forEach(function (rating) {
          var row = document.createElement('tr');
          [rating.resource, rating.contributor, rating.ratedBy,
            '★ ' + rating.rating, formatDate(rating.updatedAt)].forEach(function (value) {
            var cell = document.createElement('td');
            cell.textContent = String(value || '');
            row.appendChild(cell);
          });
          ratingBody.appendChild(row);
        });
        if (!result.data.resourceRatings || !result.data.resourceRatings.length) {
          var emptyRow = document.createElement('tr');
          var emptyCell = document.createElement('td');
          emptyCell.colSpan = 5;
          emptyCell.textContent = 'No resource ratings have been submitted.';
          emptyRow.appendChild(emptyCell);
          ratingBody.appendChild(emptyRow);
        }
        ratingTable.appendChild(ratingBody);
        var ratingWrap = document.createElement('div');
        ratingWrap.className = 'table-wrap';
        ratingWrap.appendChild(ratingTable);
        ratingsTarget.appendChild(ratingWrap);
      }
      if (reviewTarget) {
        reviewTarget.innerHTML = '';
        (result.data.recentContributions || []).forEach(function (entry) {
          var item = document.createElement('div');
          item.className = 'moderation-row';
          var text = document.createElement('span');
          text.textContent = entry.name + ' · ' + entry.type.replace(/_/g, ' ') +
            ' · ' + entry.points + ' points · ' + (entry.active ? 'active' : 'removed') +
            (entry.reason ? ' · ' + entry.reason : '');
          item.appendChild(text);
          if (entry.active) {
            var remove = document.createElement('button');
            remove.type = 'button';
            remove.className = 'btn btn-outline btn-sm';
            remove.textContent = 'Remove contribution';
            remove.addEventListener('click', function () {
              var reason = window.prompt('Enter a moderation reason (at least 10 characters):');
              if (!reason || reason.trim().length < 10) { return; }
              api('/api/admin/leadership/contributions/' + encodeURIComponent(entry.id), {
                method: 'DELETE',
                body: { reason: reason.trim() }
              }).then(function (moderation) {
                if (moderation.ok) { initAdminLeadership(); }
                else { window.alert(moderation.data.message || 'Unable to remove this contribution.'); }
              });
            });
            item.appendChild(remove);
          }
          reviewTarget.appendChild(item);
        });
      }
    });
  }

  function renderAssignmentList(items, target, role) {
    if (!target) { return; }
    target.innerHTML = '';
    if (!items || !items.length) {
      var empty = document.createElement('div');
      empty.className = 'placeholder-card';
      empty.textContent = role === 'student'
        ? 'There are no assignments for your branch yet.'
        : 'You have not published any assignments yet.';
      target.appendChild(empty);
      return;
    }
    items.forEach(function (assignment) {
      var card = document.createElement('article');
      card.className = 'card assignment-card';
      var title = document.createElement('h2');
      title.textContent = assignment.title;
      var instructions = document.createElement('p');
      instructions.textContent = assignment.description;
      var meta = document.createElement('p');
      meta.className = 'muted small';
      meta.textContent = assignment.branch + ' · Due: ' +
        (assignment.dueAt ? formatDate(assignment.dueAt) : 'No due date');
      var viewAssignment = document.createElement('a');
      viewAssignment.className = 'btn btn-outline btn-sm';
      viewAssignment.href = assignment.viewUrl || assignment.downloadUrl + '?view=1';
      viewAssignment.target = '_blank';
      viewAssignment.rel = 'noopener noreferrer';
      viewAssignment.textContent = 'View assignment';
      var download = document.createElement('a');
      download.className = 'btn btn-outline btn-sm';
      download.href = assignment.downloadUrl;
      download.textContent = 'Download assignment';
      card.appendChild(title);
      card.appendChild(instructions);
      card.appendChild(meta);
      card.appendChild(viewAssignment);
      card.appendChild(download);

      if (role === 'student') {
        var previous = assignment.mySubmission;
        var state = document.createElement('p');
        state.className = 'muted small';
        state.textContent = previous
          ? 'Submitted ' + formatDate(previous.submittedAt) + ' · version ' + previous.version +
            ' · ' + previous.fileName
          : 'Not submitted yet.';
        if (previous) {
          var viewSubmission = document.createElement('a');
          viewSubmission.className = 'btn btn-outline btn-sm';
          viewSubmission.href = previous.viewUrl || previous.downloadUrl + '?view=1';
          viewSubmission.target = '_blank';
          viewSubmission.rel = 'noopener noreferrer';
          viewSubmission.textContent = 'View submission';
          var deleteSubmission = document.createElement('button');
          deleteSubmission.type = 'button';
          deleteSubmission.className = 'btn btn-outline btn-sm btn-danger';
          deleteSubmission.textContent = 'Delete submission';
          deleteSubmission.addEventListener('click', function () {
            if (!window.confirm('Delete your submission for this assignment?')) { return; }
            api('/api/assignments/' + encodeURIComponent(assignment.id) + '/submission', {
              method: 'DELETE'
            }).then(function (result) {
              if (!result.ok) {
                window.alert(result.data.message || 'Unable to delete this submission.');
                return;
              }
              loadAssignments();
            });
          });
          card.appendChild(viewSubmission);
          card.appendChild(deleteSubmission);
        }
        var form = document.createElement('form');
        form.className = 'form assignment-submission-form';
        form.enctype = 'multipart/form-data';
        var rollLabel = document.createElement('label');
        rollLabel.textContent = 'Roll number';
        var rollInput = document.createElement('input');
        rollInput.className = 'input';
        rollInput.name = 'rollNumber';
        rollInput.required = true;
        rollInput.value = (currentUser && currentUser.rollNumber) || (previous && previous.rollNumber) || '';
        if (rollInput.value) { rollInput.readOnly = true; }
        rollLabel.appendChild(rollInput);
        var fileLabel = document.createElement('label');
        fileLabel.textContent = 'PowerPoint or image file';
        var fileInput = document.createElement('input');
        fileInput.type = 'file';
        fileInput.name = 'file';
        fileInput.className = 'input';
        fileInput.accept = '.ppt,.pptx,.png,.jpg,.jpeg,.webp';
        fileInput.required = true;
        fileLabel.appendChild(fileInput);
        var alert = document.createElement('div');
        alert.className = 'alert';
        alert.hidden = true;
        var submit = document.createElement('button');
        submit.type = 'submit';
        submit.className = 'btn btn-primary';
        submit.textContent = previous ? 'Update submission' : 'Submit assignment';
        form.appendChild(rollLabel);
        form.appendChild(fileLabel);
        form.appendChild(alert);
        form.appendChild(submit);
        form.addEventListener('submit', function (event) {
          event.preventDefault();
          if (!fileInput.files.length) { return; }
          var body = new FormData();
          body.append('rollNumber', rollInput.value);
          body.append('file', fileInput.files[0]);
          setBusy(submit, true, 'Submitting…');
          api('/api/assignments/' + encodeURIComponent(assignment.id) + '/submission', {
            method: 'PUT',
            body: body
          }).then(function (response) {
            setBusy(submit, false);
            if (!response.ok) {
              alert.textContent = response.data.message || 'Unable to submit this assignment.';
              alert.className = 'alert alert-error';
              alert.hidden = false;
              return;
            }
            loadAssignments();
          });
        });
        card.appendChild(state);
        card.appendChild(form);
      } else {
        var count = document.createElement('p');
        count.className = 'muted small';
        count.textContent = assignment.submissionCount + ' submission(s)';
        card.appendChild(count);
        (assignment.submissions || []).forEach(function (submission) {
          var submitted = document.createElement('div');
          submitted.className = 'assignment-submission-row';
          var student = document.createElement('span');
          student.textContent = submission.studentName + ' · ' + submission.rollNumber +
            ' · submitted ' + formatDate(submission.submittedAt) + ' · version ' + submission.version;
          var viewSubmission = document.createElement('a');
          viewSubmission.href = submission.viewUrl || submission.downloadUrl + '?view=1';
          viewSubmission.target = '_blank';
          viewSubmission.rel = 'noopener noreferrer';
          viewSubmission.className = 'btn btn-outline btn-sm';
          viewSubmission.textContent = 'View submission';
          var fileLink = document.createElement('a');
          fileLink.href = submission.downloadUrl;
          fileLink.className = 'btn btn-outline btn-sm';
          fileLink.textContent = 'Download';
          submitted.appendChild(student);
          submitted.appendChild(viewSubmission);
          submitted.appendChild(fileLink);
          card.appendChild(submitted);
        });
      }
      target.appendChild(card);
    });
  }

  function loadAssignments() {
    var role = document.body.getAttribute('data-role');
    var target = document.getElementById(role === 'student' ? 'studentAssignmentList' : 'facultyAssignmentList');
    if (!target) { return; }
    api('/api/assignments').then(function (result) {
      if (result.ok) { renderAssignmentList(result.data.assignments || [], target, role); }
      else { target.textContent = result.data.message || 'Unable to load assignments.'; }
    });
  }

  function initAssignments() {
    var form = document.getElementById('assignmentForm');
    if (form) {
      form.addEventListener('submit', function (event) {
        event.preventDefault();
        var alert = document.getElementById('assignmentFormAlert');
        alert.hidden = true;
        var submit = form.querySelector('button[type="submit"]');
        setBusy(submit, true, 'Publishing…');
        api('/api/assignments', { method: 'POST', body: new FormData(form) }).then(function (result) {
          setBusy(submit, false);
          if (!result.ok) {
            alert.textContent = result.data.message || 'Unable to publish assignment.';
            alert.className = 'alert alert-error';
            alert.hidden = false;
            return;
          }
          form.reset();
          loadAssignments();
        });
      });
    }
    loadAssignments();
  }

  function initNotifications() {
    var target = document.getElementById('notificationList');
    if (!target) { return; }
    api('/api/notifications').then(function (result) {
      if (!result.ok) { return; }
      var notifications = result.data.notifications || [];
      target.innerHTML = '';
      notifications.forEach(function (notification) {
        var node = document.createElement('div');
        node.className = 'alert alert-info';
        node.textContent = notification.message || 'New campus activity.';
        target.appendChild(node);
      });
    });
  }

  function initAnnouncements() {
    var form = document.getElementById('announcementForm');
    var list = document.getElementById('announcementList') ||
      document.getElementById('studentAnnouncementList') ||
      document.getElementById('facultyAnnouncementList');
    var alertBox = document.getElementById('announcementFormAlert');
    if (!list) { return; }

    function showFormAlert(type, message) {
      if (!alertBox) { return; }
      alertBox.className = 'alert alert-' + type;
      alertBox.textContent = message;
      alertBox.hidden = false;
    }

    function hideFormAlert() {
      if (!alertBox) { return; }
      alertBox.hidden = true;
      alertBox.textContent = '';
      alertBox.className = 'alert';
    }

    function renderAnnouncements(items) {
      list.innerHTML = '';
      if (!items || !items.length) {
        var empty = document.createElement('div');
        empty.className = 'empty-announcements';
        empty.textContent = 'No announcements published yet.';
        list.appendChild(empty);
        return;
      }

      items.forEach(function (item) {
        var card = document.createElement('article');
        card.className = 'announcement-card';

        var head = document.createElement('div');
        head.className = 'announcement-card-header';

        var title = document.createElement('h3');
        title.textContent = item.title;

        head.appendChild(title);

        var body = document.createElement('div');
        body.className = 'announcement-body';
        
        if (item.fileName) {
          var img = document.createElement('img');
          img.src = '/api/announcements/' + item.id + '/image';
          img.style.maxWidth = '100%';
          img.style.marginTop = '0.5rem';
          img.style.marginBottom = '0.5rem';
          img.style.borderRadius = '4px';
          body.appendChild(img);
        }
        
        if (item.message) {
          var p = document.createElement('p');
          p.textContent = item.message;
          body.appendChild(p);
        }

        var meta = document.createElement('div');
        meta.className = 'announcement-meta';
        var publisher = item.publishedBy && item.publishedBy.fullName ? item.publishedBy.fullName : 'CampusHub admin';
        meta.textContent = publisher + ' • ' + formatDate(item.createdAt);

        card.appendChild(head);
        card.appendChild(body);
        card.appendChild(meta);
        list.appendChild(card);
      });
    }

    function loadAnnouncements() {
      api('/api/announcements').then(function (result) {
        if (!result.ok) {
          list.innerHTML = '<div class="empty-announcements">Unable to load announcements right now.</div>';
          return;
        }
        renderAnnouncements(result.data.announcements || []);
      });
    }

    if (form) { form.addEventListener('submit', function (event) {
      event.preventDefault();
      hideFormAlert();

      var title = form.elements.title.value.trim();
      var message = form.elements.message ? form.elements.message.value.trim() : '';
      var audience = form.elements.audience ? form.elements.audience.value : 'all';
      var fileInput = form.elements.file;
      var file = fileInput && fileInput.files.length > 0 ? fileInput.files[0] : null;

      if (!title || (!message && !file)) {
        showFormAlert('error', 'Please add a title and either a message or an image before publishing.');
        return;
      }

      var submit = form.querySelector('button[type="submit"]');
      setBusy(submit, true, 'Publishing…');

      var formData = new FormData();
      formData.append('title', title);
      formData.append('message', message);
      formData.append('audience', audience);
      if (file) {
        formData.append('file', file);
      }

      api('/api/announcements', {
        method: 'POST',
        body: formData
      }).then(function (result) {
        setBusy(submit, false);

        if (!result.ok) {
          showFormAlert('error', result.data.message || 'Unable to publish the announcement.');
          return;
        }

        form.reset();
        showFormAlert('success', result.data.message || 'Announcement published.');
        loadAnnouncements();
      });
    }); }

    loadAnnouncements();
  }

  function initDashboard() {
    var requiredRole = document.body.getAttribute('data-role');
    var sessionState = $('#sessionState');
    var content = $('#dashContent');
    var activatePanel = initPanels();

    function fail(message) {
      if (content) { content.hidden = true; }
      if (sessionState) {
        sessionState.innerHTML = '';
        sessionState.textContent = message;
      }
    }

    // Ask the server who we are. The dashboard pages are already protected on
    // the server, so this mainly guards against expired sessions.
    api('/api/auth/me').then(function (result) {
      if (!result.ok || !result.data.user) {
        window.location.href = ROUTES.login + '?error=auth_required&next=' +
          encodeURIComponent(window.location.pathname);
        return;
      }
      currentUser = result.data.user;

      var user = result.data.user;

      // Defence in depth: a student can never render the admin dashboard,
      // even if the HTML was somehow delivered to the browser.
      if (requiredRole && user.role !== requiredRole) {
        var own = dashboardFor(user.role);
        if (own && own !== window.location.pathname) {
          window.location.href = own;
          return;
        }
        fail('Your account role (' + roleLabel(user.role) +
             ') cannot open this dashboard.');
        return;
      }

      renderUserDetails(user);
      if (sessionState) { sessionState.hidden = true; }
      if (content) { content.hidden = false; }
      if (user.role === 'admin') { initAdminManagement(); }
      if (typeof activatePanel === 'function') {
        var activePanel = (window.location.hash || '').replace('#', '') ||
          ($('.dash-panel') && $('.dash-panel').getAttribute('data-panel'));
        activatePanel(activePanel, false);
      }
      initAssignments();
      initLeadership();
      initQuestions();
      initNotifications();
      loadDashboardResources();
      var resourceSearchForm = document.getElementById('resourceSearchForm');
      if (resourceSearchForm) {
        resourceSearchForm.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
      }
    });
  }

  /* ------------------------------------------------------------------ *
   * 6. Bootstrap
   * ------------------------------------------------------------------ */
  function boot() {
    initNavToggles();
    initPasswordToggles();
    initFooterYear();
    initLogoutButtons();
    initBranchOptions();
    initCategoryOptions();

    var page = document.body.getAttribute('data-page') || '';

    var registerForm = $('#registerForm');
    if (registerForm) { initRegisterPage(registerForm); }

    var loginForm = $('#loginForm');
    if (loginForm) { initLoginPage(loginForm); }

    var isDashboard = page.indexOf('dashboard') !== -1;
    if (isDashboard) {
      initDashboard();
      initAnnouncements();
      initResourceUpload();
      initResourceSearch();
      initQuestions();
      initNotifications();
    } else {
      // Public pages: personalise the header with the current session, if any.
      api('/api/auth/me').then(function (result) {
        renderHeaderAuth(result.ok ? result.data.user : null);
      });
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
