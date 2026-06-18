import React from 'react';
import { Link } from 'react-router-dom';

function ProductCard() {
  return (
    <div className="product-card">
      <Link to="/products/1">
        <img src="" alt="Product" />
        <h3>Product Name</h3>
        <p>$0.00</p>
      </Link>
      <button>Add to Cart</button>
    </div>
  );
}

export default ProductCard;