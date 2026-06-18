export default function BookCard({ book }) {
  return (
    <div>
      <h3>{book.title}</h3>
      <p>{book.author}</p>
    </div>
  );
}