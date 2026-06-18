function CategorySelector({ value, onChange }) {
  const categories = ['Food', 'Transport', 'Entertainment', 'Utilities'];
  
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">Select category</option>
      {categories.map((cat) => (
        <option key={cat} value={cat}>{cat}</option>
      ))}
    </select>
  );
}

export default CategorySelector;