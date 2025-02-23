% Generated by roxygen2: do not edit by hand
% Please edit documentation in R/lazytensor_preprocess.R
\name{unaryop.LazyTensor}
\alias{unaryop.LazyTensor}
\title{Build a unary operation}
\usage{
unaryop.LazyTensor(
  x,
  opstr,
  opt_arg = NA,
  opt_arg2 = NA,
  res_type = NA,
  dim_res = NA
)
}
\arguments{
\item{x}{A \code{LazyTensor}, a \code{ComplexLazyTensor}, a vector of numeric values,
or a scalar value.}

\item{opstr}{A text string corresponding to an operation.}

\item{opt_arg}{An optional argument which can be a scalar value.}

\item{opt_arg2}{An optional argument which can be a scalar value.}

\item{res_type}{NA (default) or a character string among "LazyTensor" and
"ComplexLazyTensor", to specify if a change of class is required for the
result. (Useful especially when dealing with complex-to-real or
real-to-complex functions).}

\item{dim_res}{NA (default) or an integer corresponding to the inner
dimension of the output \code{LazyTensor}. If NA, \strong{dim_res} is set to the
inner dimension of the input \code{LazyTensor}.}
}
\value{
An object of class "LazyTensor" or "ComplexLazyTensor".
}
\description{
Symbolically applies \strong{opstr} operation to \strong{x}.
}
\examples{
\dontrun{
x <- matrix(runif(150 * 3), 150, 3) # arbitrary R matrix, 150 rows, 3 columns
x_i <- LazyTensor(x, index = 'i')   # creating LazyTensor from matrix x, 
                                    # indexed by 'i'
una_x <- unaryop.LazyTensor(x_i, "Minus")   # symbolic matrix

una2_x <- unaryop.LazyTensor(x_i, "Pow", opt_arg = 3)  # symbolic matrix

# example with not NA dim_res:
## set dim_res to 1 because the "Norm2" operation results on a (symbolic) scalar
una3_x <- unaryop.LazyTensor(x, "Norm2",
                             res_type = "LazyTensor",
                             dim_res = 1)
}
}
\author{
Chloe Serre-Combe, Amelie Vernay
}
