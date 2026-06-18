export default function Register() {
  const handleSubmit = (e) => {
    e.preventDefault();
  };

  return (
    <div>
      <h1>Register</h1>
      <form onSubmit={handleSubmit}>
        <input type="text" placeholder="Username" required />
        <input type="email" placeholder="Email" required />
        <input type="password" placeholder="Password" required />
        <button type="submit">Register</button>
      </form>
    </div>
  );
}