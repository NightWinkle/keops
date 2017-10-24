from libds import cudaconv,cudagradconv,cudagradgradconv
import torch
import numpy
from torch.autograd import Variable

# Computation are made in float32
dtype = torch.FloatTensor 


# See github.com/pytorch/pytorch/pull/1016 , pytorch.org/docs/0.2.0/notes/extending.html
# for reference on the forward-backward syntax
class KernelProduct(torch.autograd.Function):
	""" This class implement an isotropic-radial kernel matrix product.
		If:
		- s   is a scale   ("sigma")
		- x_i is an N-by-D array  (i from 1 to N)
		- y_j is an M-by-D array  (j from 1 to M)
		- b_j is an M-by-E array  (j from 1 to M)
		
		Then:
		KernelProduct( s, x, y, b) is an N-by-E array,
		whose i-th line is given by
		KernelProduct( s, x, y, b)_i = \sum_j f_s( |x_i-y_j|^2 ) b_j .
		
		f is a real-valued function which encodes the kernel operation:
		
		                 k_s(x,y)  =  f_s( |x_i-y_j|^2 )
		
		Computations are performed with CUDA on the GPU, using the 'libds' files:
		the operator KernelProduct is differentiable 2 times, with all variable combinations.                 
		This code was designed for memory efficiency: the kernel matrix is computed "one tile
		after another", and never stored in memory, be it on the GPU or on the RAM.
		
		N.B.: f and its derivatives f', f'' are hardcoded in the 'libds/kernels.cx' file.
		
		Author: Jean Feydy
	"""
	
	@staticmethod
	def forward(ctx, s, x, y, b):
		""" 
			KernelProduct(s, x, y, b)_i = \sum_j k_s(  x_i , y_j  ) b_j
			                            = \sum_j f_s( |x_i-y_j|^2 ) b_j .
		"""
		# save everything to compute the gradient
		# ctx.ss = s ; ctx.xx = x ; ctx.yy = y ; ctx.bb = b # Too naive! We need to save VARIABLES.
		# N.B.: relying on the "ctx.saved_variables" attribute is necessary
		#       if you want to be able to differentiate the output of the backward
		#       once again. It helps pytorch to keep track of "who is who".
		ctx.save_for_backward( s, x, y, b ) # Call at most once in the "forward".
		
		# init gamma which contains the output of the convolution K_xy @ b
		ctx.gamma  = torch.Tensor( x.size()[0] * b.size()[1] ).type(dtype)
		# Inplace CUDA routine
		cudaconv.cuda_conv(x.numpy(),y.numpy(),b.numpy(),ctx.gamma.numpy(),s.numpy()) 
		ctx.gamma  = ctx.gamma.view( x.size()[0], b.size()[1] )
		return ctx.gamma
	
	@staticmethod
	def backward(ctx, a):
		""" Backward scheme: 
			given a dual output vector "a_i" represented by a N-by-D array (i from 1 to N),
			outputs :
			- \partial_s K(s,x,y,b) . a, which is a float number (NOT IMPLEMENTED YET)
			- \partial_x K(s,x,y,b) . a, which is a N-by-D array
			- \partial_y K(s,x,y,b) . a, which is a M-by-D array
			- \partial_b K(s,x,y,b) . a, which is a M-by-E array, equal to K(s,y,x,a).		
		"""
		(ss, xx, yy, bb) = ctx.saved_variables # Unwrap the saved variables
		
		# In order to get second derivatives, we encapsulated the cudagradconv.cuda_gradconv
		# routine in another torch.autograd.Function object:
		kernelproductgrad_x = KernelProductGrad_x().apply 
		
		# Compute \partial_s K(s,x,y,b) . a   -------------NOT IMPLEMENTED YET-------------------
		grad_s = None
		
		# Compute \partial_x K(s,x,y,b) . a   --------------------------------------------------- 
		# We're looking for the gradient with respect to x of
		# < a, K(s,x,y,b) >  =  \sum_{i,j} f_s( |x_i-y_j|^2 ) < a_i, b_j >
		# kernelproductgrad_x computes the gradient, with respect to the 3rd variable x, of trace(
		grad_x = kernelproductgrad_x( ss, a,  #     a^T
								      xx, yy, #   @ K(x,y)
								      bb    ) #   @ b )
		
		# Compute \partial_y K(s,x,y,b) . a   --------------------------------------------------- 
		# We're looking for the gradient with respect to y of
		# < a, K(s,x,y,b) >  =  \sum_{i,j} f_s( |x_i-y_j|^2 ) < a_i, b_j >
		# Thanks to the symmetry in (x,a) and (y,b) of the above formula,
		# we can use the same cuda code as the one that was used for grad_x:
		# kernelproductgrad_x computes the gradient, with respect to the 3rd variable y, of trace(
		grad_y = kernelproductgrad_x( ss, bb, #     b^T
								      yy, xx, #   @ K(y,x)
								      a     ) #   @ a )
		
		# Compute \partial_b K(s,x,y,b) . a   --------------------------------------------------- 
		ctx.Kt = KernelProduct().apply # Will be used to compute the kernel "transpose"
		grad_b = ctx.Kt(ss,      # We use the same kernel scale
		                yy, xx,  # But we compute K_yx, instead of K_xy
		                a     )  # And multiply it with a         
		
		return (grad_s, grad_x, grad_y, grad_b)

class KernelProductGrad_x(torch.autograd.Function):
	""" This class implements the gradient of the above operator
		'KernelProduct' with respect to its second variable, 'x'.
		If:
		- s   is a scale   ("sigma")
		- a_i is an N-by-E array  (i from 1 to N)
		- x_i is an N-by-D array  (i from 1 to N)
		- y_j is an M-by-D array  (j from 1 to M)
		- b_j is an M-by-E array  (j from 1 to M)
		
		Then:
		KernelProductGrad_x( s, a, x, y, b) is an N-by-D array,
		whose i-th line is given by
		KernelProduct( s, a, x, y, b)_i = \sum_j f_s'( |x_i-y_j|^2 ) * < a_i, b_j> * 2(x_i-y_j).
		
		This class wasn't designed to be used by end-users, but to provide the second derivatives
		of the operator 'KernelProduct', encoded in KernelProductGrad_x's backwars operator.
		
		f is a real-valued function which encodes the kernel operation:
		
		                 k_s(x,y)  =  f_s( |x_i-y_j|^2 )
		
		Computations are performed with CUDA on the GPU, using the 'libds' files.                 
		This code was designed for memory efficiency: the kernel matrix is computed "one tile
		after another", and never stored in memory, be it on the GPU or on the RAM.
		
		N.B.: f and its derivatives f', f'' are hardcoded in the 'libds/kernels.cx' file.
		
		Author: Jean Feydy
	"""
	
	@staticmethod
	def forward(ctx, s, a, x, y, b):
		""" 
		KernelProduct(s, a, x, y, b)_i = \sum_j f_s'( |x_i-y_j|^2 ) * < a_i, b_j> * 2(x_i-y_j).
		"""
		# save everything to compute the gradient
		#ctx.ss = s ; ctx.aa = a ; ctx.xx = x # TOO NAIVE!
		#ctx.yy = y ; ctx.bb = b              # We should save variables explicitly
		# N.B.: relying on the "ctx.saved_variables" attribute is necessary
		#       if you want to be able to differentiate the output of the backward
		#       once again. It helps pytorch to keep track of "who is who".
		#       As we haven't implemented the "3rd" derivative of KernelProduct,
		#       this formulation is not strictly necessary here... 
		#       But I think it is good practice anyway.
		ctx.save_for_backward( s, a, x, y, b )   # Call at most once in the "forward".
		
		# init grad_x which contains the output
		ctx.grad_x = torch.Tensor(x.numel()).type(dtype) #0d array
		# We're looking for the gradient with respect to x of
		# < a, K(s,x,y,b) >  =  \sum_{i,j} f_s( |x_i-y_j|^2 ) < a_i, b_j >
		# Cudagradconv computes the gradient, with respect to x, of trace(
		cudagradconv.cuda_gradconv( a.numpy(),            #     a^T
								    x.numpy(), y.numpy(), #   @ K(x,y)
								    b.numpy(),            #   @ b )
								    ctx.grad_x.numpy(),   # Output array
								    s.numpy())            # Kernel scale parameter
		ctx.grad_x = ctx.grad_x.view(x.shape)
		
		return ctx.grad_x
	
	@staticmethod
	def backward(ctx, e):
		""" Backward scheme: 
			given a dual output vector "e_i" represented by a N-by-D array (i from 1 to N),
			outputs :
			- \partial_s Kx(s,a,x,y,b) . e, which is a float number (NOT IMPLEMENTED YET)
			- \partial_a Kx(s,a,x,y,b) . e, which is a N-by-E array
			- \partial_x Kx(s,a,x,y,b) . e, which is a N-by-D array
			- \partial_y Kx(s,a,x,y,b) . e, which is a M-by-D array
			- \partial_b Kx(s,a,x,y,b) . e, which is a M-by-E array, equal to K(s,y,x,a).		
		"""
		(ss, aa, xx, yy, bb) = ctx.saved_variables # Unwrap the saved variables
		
		# Compute \partial_s Kx(s,a,x,y,b) . e   ------------NOT IMPLEMENTED YET-----------------
		grad_xs = None
		
		# Compute \partial_a Kx(s,a,x,y,b) . e   ------------------------------------------------ 
		# We're looking for the gradient with respect to a of
		#
		# < e, K(s,a,x,y,b) >  =  \sum_{i,j} f_s'( |x_i-y_j|^2 ) * < a_i, b_j > * 2 < e_i, x_i-y_j>,
		#
		# which is an N-by-E array g_i (i from 1 to N), where each line is equal to
		#
		# g_i  =  \sum_j 2* f_s'( |x_i-y_j|^2 ) * < e_i, x_i-y_j> * b_j
		#
		# This is what cuda_gradconv_xa is all about:
		
		grad_xa = torch.Tensor( aa.numel() ).type(dtype) #0d array
		cudagradgradconv.cuda_gradconv_xa( e.data.numpy(),
										   aa.data.numpy(),
										   xx.data.numpy(), yy.data.numpy(),
										   bb.data.numpy(),
										   grad_xa.numpy(),  # Output array
										   ss.data.numpy()   ) 
		grad_xa  = Variable(grad_xa.view( aa.size()[0], aa.size()[1] ))
		
		# Compute \partial_x Kx(s,a,x,y,b) . e   ------------------------------------------------ 
		# We're looking for the gradient with respect to x of
		#
		# < e, K(s,a,x,y,b) >  =  \sum_{i,j} f_s'( |x_i-y_j|^2 ) * < a_i, b_j > * 2 < e_i, x_i-y_j>,
		#
		# which is an N-by-D array g_i (i from 1 to N), where each line is equal to
		#
		# g_i  =  2* \sum_j < a_i, b_j > * [                       f_s'(  |x_i-y_j|^2 ) * e_i
		#                                  + 2* < x_i-y_j, e_i > * f_s''( |x_i-y_j|^2 ) * (x_i-y_j) ]
		#
		# This is what cuda_gradconv_xx is all about:
		
		grad_xx = torch.Tensor( xx.numel() ).type(dtype) #0d array
		cudagradgradconv.cuda_gradconv_xx(  e.data.numpy(),
										   aa.data.numpy(),
										   xx.data.numpy(), yy.data.numpy(),
										   bb.data.numpy(),
										   grad_xx.numpy(),  # Output array
										   ss.data.numpy()   ) 
		grad_xx  = Variable(grad_xx.view( xx.size()[0], xx.size()[1] ))
		
		# Compute \partial_y Kx(s,a,x,y,b) . e   ------------------------------------------------ 
		# We're looking for the gradient with respect to y of
		#
		# < e, K(s,a,x,y,b) >  =  \sum_{i,j} f_s'( |x_i-y_j|^2 ) * < a_i, b_j > * 2 < e_i, x_i-y_j>,
		#
		# which is an M-by-D array g_j (j from 1 to M), where each line is equal to
		#
		# g_j  = -2* \sum_i < a_i, b_j > * [                       f_s'(  |x_i-y_j|^2 ) * e_i
		#    "don't forget the -2 !"       + 2* < x_i-y_j, e_i > * f_s''( |x_i-y_j|^2 ) * (x_i-y_j) ]
		#
		# This is what cuda_gradconv_xy is all about:
		
		grad_xy = torch.Tensor( yy.numel() ).type(dtype) #0d array
		cudagradgradconv.cuda_gradconv_xy(  e.data.numpy(),
										   aa.data.numpy(),
										   xx.data.numpy(), yy.data.numpy(),
										   bb.data.numpy(),
										   grad_xy.numpy(),  # Output array
										   ss.data.numpy()   ) 
		grad_xy  = Variable(grad_xy.view( yy.size()[0], yy.size()[1] ))
		
		# Compute \partial_b Kx(s,a,x,y,b) . e   ------------------------------------------------ 
		# We're looking for the gradient with respect to b of
		#
		# < e, K(s,a,x,y,b) >  =  \sum_{i,j} f_s'( |x_i-y_j|^2 ) * < a_i, b_j > * 2 < e_i, x_i-y_j>,
		#
		# which is an M-by-E array g_j (j from 1 to M), where each line is equal to
		#
		# g_j  =  \sum_i 2* f_s'( |x_i-y_j|^2 ) * < e_i, x_i-y_j> * a_i
		#
		# This is what cuda_gradconv_xb is all about:
		
		grad_xb = torch.Tensor( bb.numel() ).type(dtype) #0d array
		cudagradgradconv.cuda_gradconv_xb(  e.data.numpy(),
										   aa.data.numpy(),
										   xx.data.numpy(), yy.data.numpy(),
										   bb.data.numpy(),
										   grad_xb.numpy(),  # Output array
										   ss.data.numpy()   ) 
		grad_xb  = Variable(grad_xb.view( bb.size()[0], bb.size()[1] ))
		
		
		return (grad_xs, grad_xa, grad_xx, grad_xy, grad_xb)



if __name__ == "__main__":
	from visualize import make_dot
	
	backend = "libds" # Other value : 'pytorch'
	
	if   backend == "libds" :
		kernel_product = KernelProduct().apply
	elif backend == "pytorch" :
		def kernel_product(s, x, y, b) :
			x_col = x.unsqueeze(1) # Theano : x.dimshuffle(0, 'x', 1)
			y_lin = y.unsqueeze(0) # Theano : y.dimshuffle('x', 0, 1)
			sq    = torch.sum( (x_col - y_lin)**2 , 2 )
			K_xy  = torch.exp( -sq / (s**2))
			return K_xy @ b
			
			
			
	#--------------------------------------------------#
	# Init variables to get a minimal working example:
	#--------------------------------------------------#
	dtype = torch.FloatTensor
	
	x = .6 * torch.linspace(0,5,15   ).type(dtype).view(5,3)
	x = torch.autograd.Variable(x, requires_grad = True)
	
	y = .2 * torch.linspace(0,5,15   ).type(dtype).view(5,3)
	y = torch.autograd.Variable(y, requires_grad = True)
	
	b = .6 * torch.linspace(-.2,.2,15).type(dtype).view(5,3)
	b = torch.autograd.Variable(b, requires_grad = True)
	
	s = torch.Tensor([2.5]).type(dtype)
	s = torch.autograd.Variable(s, requires_grad = True)
	
	#--------------------------------------------------#
	# check the class KernelProduct
	#--------------------------------------------------#
	def Ham(q,p) :
		Kq_p  = kernel_product(s,q,q,p)
		make_dot(Kq_p, {'x':q, 'b':p, 's':s}).render('graphs/Kqp_'+backend+'.pdf', view=True)
		return torch.dot( p.view(-1), Kq_p.view(-1) )
	
	ham0   = Ham(x, b)
	make_dot(ham0, {'x':x, 'b':b, 's':s}).render('graphs/ham0_'+backend+'.pdf', view=True)
	
	print("Ham0:")
	print(ham0)
	
	grad_x = torch.autograd.grad(ham0,x,create_graph = True)[0]
	grad_b = torch.autograd.grad(ham0,b,create_graph = True)[0]
	
	if False :
		def to_check( X, Y, B ):
			return kernel_product(s, X, Y, B)
		gc = torch.autograd.gradcheck(to_check, inputs=(x, y, b) , eps=1e-4, atol=1e-3, rtol=1e-3 )
		print('Gradcheck for Hamiltonian: ',gc)
		print('\n')

	#--------------------------------------------------#
	# check that we are able to compute derivatives with autograd
	#--------------------------------------------------#
	
	grad_b2 = (grad_x).sum()
	make_dot(grad_b2, {'x':x, 'b':b, 's':s}).render('graphs/grad_b2_'+backend+'.pdf', view=True)
	
	grad_bb  = torch.autograd.grad(grad_b2,x,create_graph = True)[0]
	make_dot(grad_bb, {'x':x, 'b':b, 's':s}).render('graphs/grad_bb_'+backend+'.pdf', view=True)
	print(grad_bb)
	
	#print('derivative of sin(ham): ',p1.grad)

	#--------------------------------------------------#
	# check that we are able to compute derivatives with autograd
	#--------------------------------------------------#
	if False :
		q1 = .6 * torch.linspace(0,5,15).type(dtype).view(5,3)
		q1 = torch.autograd.Variable(q1, requires_grad = True)

		p1 = .5 * torch.linspace(-.2,.2,15).type(dtype).view(5,3)
		p1 = torch.autograd.Variable(p1, requires_grad = True)
		sh = torch.sin(ham)

		gsh = torch.autograd.grad( sh , p1, create_graph = True)[0]
		print('derivative of sin(ham): ', gsh)
		print(gsh.volatile)

		ngsh = gsh.sum()

		ggsh = torch.autograd.grad( ngsh , p1)
		print('derivative of sin(ham): ', ggsh)
