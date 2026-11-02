const form = document.querySelector('#login-form');
const error = document.querySelector('#login-error');
form.addEventListener('submit', async event => {
  event.preventDefault();
  const button = form.querySelector('button');
  button.disabled = true;
  button.textContent = 'Signing in…';
  error.hidden = true;
  try {
    const response = await fetch('/api/login', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin',
      body: JSON.stringify({ username: form.username.value, password: form.password.value }),
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || 'Sign-in failed');
    location.replace('/');
  } catch (cause) {
    error.textContent = cause.message;
    error.hidden = false;
    button.disabled = false;
    button.textContent = 'Sign in';
  }
});
