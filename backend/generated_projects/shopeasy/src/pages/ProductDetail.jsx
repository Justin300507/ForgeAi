import React from 'react';

function ProductDetail() {
  return (
    <div>
      <h1>Product Details</h1>
      <div className="product-detail">
        <img src="" alt="Product" />
        <h2>Product Name</h2>
        <p>Price: $0.00</p>
        <p>Description: Lorem ipsum</p>
        <button>Add to Cart</button>
      </div>
    </div>
  );
}

export default ProductDetail;