export default function Login({ setUser }) {
  const handleSubmit = (e) => {
    e.preventDefault();
    setUser({ id: '1', username: 'testuser' });
  };

  return (
    <div>
      <h1>Login</h1>
      <form onSubmit={handleSubmit}>
        <input type="text" placeholder="Username" required />
        <input type="password" placeholder="Password" required />
        <button type="submit">Login</button>
      </form>
    </div>
  );
}