export default function UserList() {
  const users = [
    { id: 1, username: 'user1', online: true },
    { id: 2, username: 'user2', online: false }
  ];

  return (
    <div>
      <h3>Users</h3>
      <ul>
        {users.map(user => (
          <li key={user.id}>
            {user.username}
            <StatusIndicator online={user.online} />
          </li>
        ))}
      </ul>
    </div>
  );
}